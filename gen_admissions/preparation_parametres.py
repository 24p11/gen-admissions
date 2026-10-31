"""Outils d'extraction des paramètres (format version 2, voir parametres/DICTIONNAIRE.md).

Ce module rassemble les fonctions mises au point dans le carnet de bord. Il
suit un schéma commun à toutes les tables :

1. une requête SQL ramène des lignes brutes (séjours, RUM, venues) pour une
   année ;
2. une fonction « effectifs » les transforme en comptages par case ;
3. les comptages des années sont additionnés ;
4. une fonction « depuis_effectifs » normalise (seuil, catégories rares),
   calcule le rang quand il y en a un, et ajoute les lignes de repli.

Les effectifs sont des fichiers de travail (ils contiennent des nombres de
séjours) ; seuls les fichiers de proportions produits à l'étape 4 sont
diffusés.
"""

from __future__ import annotations

from typing import Iterable, Sequence

import numpy as np
import pandas as pd
import yaml

# ---------------------------------------------------------------------------
# Réglages communs
# ---------------------------------------------------------------------------

SEUIL_EFFECTIF = 100               # séjours, sur la période d'observation (5 ans)
PART_MIN_SERVICE_HABITUEL = 0.10   # part à partir de laquelle un service est de rang 1

CLASSES_AGE = ["0-17", "18-64", "65-74", "75+"]
CLASSES_AGE_DUREE = ["0-59", "60+"]
TRANCHES_H = ["0-1", "1-2", "2-4", "4-6", "6-12", "12-24", "24+"]
_BORNES_TRANCHES_H = [0, 1, 2, 4, 6, 12, 24, np.inf]

SANS_SEJOUR = ["consultation", "transfert", "deces", "parti"]
AVEC_SEJOUR = ["hospitalisation", "mono_uhcd"]


def classe_age(age: float) -> str:
    """Classe d'âge commune (âge révolu)."""
    return "0-17" if age <= 17 else "18-64" if age <= 64 else "65-74" if age <= 74 else "75+"


def classe_age_duree(age: float) -> str:
    """Classe d'âge des tables par GHM complet."""
    return "0-59" if age <= 59 else "60+"


def tranche_heures(duree_h: pd.Series) -> pd.Series:
    """Tranche horaire d'une durée en heures (borne basse incluse)."""
    return pd.cut(duree_h, bins=_BORNES_TRANCHES_H, labels=TRANCHES_H, right=False, include_lowest=True).astype(str)


# ---------------------------------------------------------------------------
# Normalisation, rang, repli
# ---------------------------------------------------------------------------

def normaliser(df: pd.DataFrame, colonnes_bloc: Sequence[str], colonne_effectif: str = "n",
               seuil: int = SEUIL_EFFECTIF) -> pd.DataFrame:
    """Transforme des effectifs en parts par bloc, avec la règle des catégories rares.

    Les lignes sous le seuil d'un même bloc reçoivent chacune une part égale à
    leur masse totale divisée par leur nombre : le volume des cas rares est
    conservé sans qu'aucune proportion individuelle sous le seuil ne soit
    transmise. Renvoie les colonnes du bloc, les colonnes de catégorie,
    ``part`` et ``observe`` (True si la ligne était au-dessus du seuil).
    ``observe`` sert au calcul du rang et ne doit pas être écrite dans les
    fichiers.
    """
    df = df.copy()
    colonnes_bloc = list(colonnes_bloc)
    cles = [df[c] for c in colonnes_bloc]
    total = df.groupby(cles)[colonne_effectif].transform("sum")
    rare = df[colonne_effectif] < seuil
    df["observe"] = ~rare
    masse_rare = df[colonne_effectif].where(rare, 0).groupby(cles).transform("sum")
    nb_rare = rare.groupby(cles).transform("sum")
    df["part"] = np.where(rare, masse_rare / nb_rare.replace(0, np.nan), df[colonne_effectif]) / total
    return df.drop(columns=[colonne_effectif]).round({"part": 6})


def attribuer_rang(t: pd.DataFrame, part_min: float = PART_MIN_SERVICE_HABITUEL,
                   masque: pd.Series | None = None) -> pd.Series:
    """Rang des services : 1 habituel (part >= part_min), 2 exceptionnel (observé
    mais sous part_min), 3 complété par la règle des catégories rares.

    ``masque`` : lignes auxquelles le rang s'applique (les autres reçoivent une
    case vide), par exemple les suites qui sont des services et non « sortie ».
    """
    if masque is None:
        masque = pd.Series(True, index=t.index)
    rang = np.select([~masque, ~t["observe"], t["part"] >= part_min], [np.nan, 3, 1], default=2)
    return pd.Series(rang, index=t.index).astype("Int64")


def table_proportions(effectifs: pd.DataFrame, colonnes_bloc: Sequence[str],
                      colonnes_categorie: Sequence[str],
                      replis: Iterable[Sequence[str]] = (),
                      seuil: int = SEUIL_EFFECTIF) -> pd.DataFrame:
    """Table de proportions avec ses lignes de repli.

    ``colonnes_bloc`` : contexte du niveau le plus fin ; ``colonnes_categorie`` :
    ce qui est tiré ; ``replis`` : liste de sous-ensembles de ``colonnes_bloc``,
    du plus détaillé au plus général. Pour chaque repli, les effectifs sont
    sommés sur les colonnes absentes, normalisés, et les colonnes absentes
    sont laissées vides. Renvoie les colonnes du bloc, les catégories et
    ``part`` (sans ``observe``).
    """
    colonnes_bloc = list(colonnes_bloc)
    colonnes_categorie = list(colonnes_categorie)
    morceaux = [normaliser(effectifs, colonnes_bloc, seuil=seuil)]
    for sous_bloc in replis:
        sous_bloc = list(sous_bloc)
        eff = effectifs.groupby(sous_bloc + colonnes_categorie, as_index=False)["n"].sum()
        morceaux.append(normaliser(eff, sous_bloc, seuil=seuil))
    colonnes = colonnes_bloc + colonnes_categorie + ["part"]
    return pd.concat(morceaux, ignore_index=True).reindex(columns=colonnes).fillna({c: "" for c in colonnes_bloc})


def additionner(effectifs_par_annee: Iterable[pd.DataFrame], colonnes: Sequence[str]) -> pd.DataFrame:
    """Somme des effectifs de plusieurs années (mêmes colonnes de contexte + ``n``)."""
    return (pd.concat(list(effectifs_par_annee), ignore_index=True)
            .groupby(list(colonnes), as_index=False)["n"].sum())


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------

def services_vers_yaml(df: pd.DataFrame, chemin: str | None = None) -> str:
    """Transforme un tableau de services en bloc YAML ``services:``.

    Colonnes attendues : ``service`` (le code, qui devient la clé du bloc),
    puis au choix ``libelle``, ``specialite``, ``lits``, ``soins_critiques``
    (texte « rea,usc » ou « usi », transformé en liste), ``occupation_visee``,
    ``age_min``, ``age_max``, ``sexe``. Les cases vides sont omises.
    """
    entiers = {"lits", "age_min", "age_max"}
    services: dict[str, dict] = {}
    for ligne in df.to_dict(orient="records"):
        code = str(ligne.pop("service"))
        bloc: dict = {}
        for cle, valeur in ligne.items():
            if pd.isna(valeur):
                continue
            if cle in entiers:
                valeur = int(valeur)
            elif cle == "soins_critiques":
                valeur = [v.strip() for v in str(valeur).split(",") if v.strip()]
            elif cle == "occupation_visee":
                valeur = float(valeur)
            else:
                valeur = str(valeur)
            bloc[cle] = valeur
        services[code] = bloc
    texte = yaml.safe_dump({"services": services}, allow_unicode=True, sort_keys=False, default_flow_style=False)
    if chemin:
        with open(chemin, "w", encoding="utf-8") as f:
            f.write(texte)
    return texte


# ---------------------------------------------------------------------------
# Venues aux urgences (venues_urgences.csv)
# ---------------------------------------------------------------------------

COLONNES_VENUES = ["periode", "classe_age", "situation", "gravite", "racine_ghm"]


def situation_venue(v: pd.DataFrame) -> pd.Series:
    """Situation de chaque venue. Le séjour chaîné fait foi ; le mode de sortie du
    RPU ne sert qu'aux venues sans séjour.

    Colonnes attendues : ``nb_rum`` et ``nb_rum_hors_uhcd`` (vides sans séjour
    chaîné), ``mode_sortie_rpu`` (6 mutation interne, 7 transfert, 8 domicile,
    9 décès). Un 6 (admission sur place) sans séjour retrouvé est un devenir
    ``inconnu`` : défaut de chaînage, exclu du calcul et compté.
    """
    a_sejour = v["nb_rum"].fillna(0) > 0
    hors_uhcd = v["nb_rum_hors_uhcd"].fillna(0) > 0
    mode = v["mode_sortie_rpu"].astype(str).str.strip()
    return pd.Series(np.select(
        [a_sejour & hors_uhcd, a_sejour & ~hors_uhcd, mode.eq("9"), mode.eq("7"), mode.eq("6")],
        ["hospitalisation", "mono_uhcd", "deces", "transfert", "inconnu"],
        default="consultation"), index=v.index)


def gravite_ccmu(ccmu: pd.Series) -> pd.Series:
    """CCMU en texte '1' à '5' ; P rattachée à 1, D à 5 ; inconnue → ''."""
    g = ccmu.astype(str).str.strip().str.upper().replace({"P": "1", "D": "5"})
    return g.where(g.isin(list("12345")), "")


def effectifs_venues(v: pd.DataFrame) -> pd.DataFrame:
    """Effectifs par (mois, classe d'âge, situation, gravité, racine) pour une année.

    Colonnes attendues en plus de celles de ``situation_venue`` : ``periode``,
    ``classe_age``, ``ccmu``, ``ghm`` (vide sans séjour). La gravité n'est
    remplie que pour les situations sans séjour, la racine que pour les
    situations avec séjour. Sont exclus, et comptés : les venues de devenir
    ``inconnu`` (admission sur place sans séjour retrouvé) et les séjours
    chaînés sans GHM (non groupés).
    """
    v = v.copy()
    v["situation"] = situation_venue(v)
    inconnu = v["situation"].eq("inconnu")
    if inconnu.any():
        print(f"{inconnu.mean():.1%} de venues au devenir inconnu (admission codée sans séjour retrouvé), exclues")
    v = v[~inconnu]
    avec_sejour = v["situation"].isin(AVEC_SEJOUR)
    v["gravite"] = np.where(avec_sejour, "", gravite_ccmu(v["ccmu"]))
    v["racine_ghm"] = np.where(avec_sejour, v["ghm"].astype(str).str[:5], "")
    v = v[~(avec_sejour & v["ghm"].isna())]
    return v.groupby(COLONNES_VENUES).size().reset_index(name="n")


def venues_depuis_effectifs(effectifs: pd.DataFrame) -> pd.DataFrame:
    """venues_urgences.csv : bloc mois × classe d'âge, repli classe d'âge seule."""
    return table_proportions(effectifs, ["periode", "classe_age"], ["situation", "gravite", "racine_ghm"],
                             replis=[["classe_age"]])


# ---------------------------------------------------------------------------
# Premier service par racine (premier_service_racine.csv)
# ---------------------------------------------------------------------------

def premier_service_depuis_effectifs(effectifs: pd.DataFrame) -> pd.DataFrame:
    """premier_service_racine.csv à partir d'effectifs (racine_ghm, classe_age,
    specialite, n) : parts, rang, et lignes de repli par racine seule."""
    morceaux = []
    for bloc in (["racine_ghm", "classe_age"], ["racine_ghm"]):
        eff = effectifs.groupby(bloc + ["specialite"], as_index=False)["n"].sum()
        t = normaliser(eff, bloc)
        t["rang"] = attribuer_rang(t)
        morceaux.append(t)
    colonnes = ["racine_ghm", "classe_age", "specialite", "part", "rang"]
    return pd.concat(morceaux, ignore_index=True).reindex(columns=colonnes).fillna({"classe_age": ""})


# ---------------------------------------------------------------------------
# Table de passage (table_passage.csv)
# ---------------------------------------------------------------------------

BLOCS_PASSAGE = {
    "complet": ["service", "precedent", "classe_age", "mode_entree"],
    "service": ["service"],
}
COLONNES_CONTEXTE_PASSAGE = ["niveau", "service", "precedent", "classe_age", "mode_entree", "suite"]


def etapes_sejours(rum: pd.DataFrame) -> pd.DataFrame:
    """Découpe les séjours en étapes : une ligne par passage de spécialité, avec
    le service précédent (``AUCUN`` au début) et la suite (``sortie`` à la fin).

    Colonnes attendues : ``id_sejour``, ``rang``, ``specialite`` (UHCD déjà
    retirée), ``classe_age``, ``mode_entree``. Les RUM consécutifs de même
    spécialité sont fusionnés.
    """
    df = rum.sort_values(["id_sejour", "rang"]).reset_index(drop=True)
    meme_sejour = df["id_sejour"].eq(df["id_sejour"].shift())
    meme_spec = df["specialite"].eq(df["specialite"].shift())
    df = df[~(meme_sejour & meme_spec)].reset_index(drop=True)
    avant = df["id_sejour"].eq(df["id_sejour"].shift(1))
    apres = df["id_sejour"].eq(df["id_sejour"].shift(-1))
    df["precedent"] = df["specialite"].shift(1).where(avant, "AUCUN")
    df["suite"] = df["specialite"].shift(-1).where(apres, "sortie")
    return df.rename(columns={"specialite": "service"})[
        ["id_sejour", "service", "precedent", "classe_age", "mode_entree", "suite"]]


def effectifs_passage(etapes: pd.DataFrame) -> pd.DataFrame:
    """Compte les transitions aux deux niveaux. Colonnes : contexte + ``n``."""
    morceaux = []
    for niveau, colonnes in BLOCS_PASSAGE.items():
        n = etapes.groupby(colonnes + ["suite"]).size().reset_index(name="n")
        n["niveau"] = niveau
        morceaux.append(n)
    return (pd.concat(morceaux, ignore_index=True)
            .reindex(columns=COLONNES_CONTEXTE_PASSAGE + ["n"]).fillna(""))


def table_passage_depuis_effectifs(effectifs: pd.DataFrame) -> pd.DataFrame:
    """table_passage.csv : normalisation par niveau et rang des suites."""
    morceaux = []
    for niveau, colonnes in BLOCS_PASSAGE.items():
        e = effectifs[effectifs["niveau"] == niveau]
        t = normaliser(e, colonnes)
        t["rang"] = attribuer_rang(t, masque=t["suite"].ne("sortie"))
        morceaux.append(t)
    return (pd.concat(morceaux, ignore_index=True)
            .reindex(columns=COLONNES_CONTEXTE_PASSAGE + ["part", "rang"]))


# ---------------------------------------------------------------------------
# Soins critiques par GHM (soins_critiques_ghm.csv)
# ---------------------------------------------------------------------------

PARTS_SOINS_CRITIQUES = ["part_rea", "part_usi", "part_usc"]


def soins_critiques_depuis_effectifs(fin: pd.DataFrame, ghm: pd.DataFrame, racine: pd.DataFrame,
                                     seuil: int = SEUIL_EFFECTIF) -> pd.DataFrame:
    """soins_critiques_ghm.csv avec le repli appliqué à la fabrication.

    ``fin`` : (ghm, classe_age_duree, n, part_rea, part_usi, part_usc) ;
    ``ghm`` : idem par GHM seul ; ``racine`` : (racine_ghm, part_*). Une case
    sous le seuil prend les parts du GHM seul, puis de la racine.
    """
    t = fin.merge(ghm[["ghm", "n"] + PARTS_SOINS_CRITIQUES], on="ghm", suffixes=("", "_ghm"))
    for p in PARTS_SOINS_CRITIQUES:
        t[p] = t[p].where(t["n"] >= seuil, t[p + "_ghm"])
    t["racine_ghm"] = t["ghm"].str[:5]
    t = t.merge(racine[["racine_ghm"] + PARTS_SOINS_CRITIQUES], on="racine_ghm", suffixes=("", "_racine"))
    for p in PARTS_SOINS_CRITIQUES:
        t[p] = t[p].where(t["n_ghm"] >= seuil, t[p + "_racine"])
    return t[["ghm", "classe_age_duree"] + PARTS_SOINS_CRITIQUES].round(6)


# ---------------------------------------------------------------------------
# Contrôles
# ---------------------------------------------------------------------------

def verifier_table(t: pd.DataFrame, colonnes_bloc: Sequence[str]) -> None:
    """Vérifie qu'une table de proportions somme à 1 dans chaque bloc."""
    sommes = t.groupby(list(colonnes_bloc))["part"].sum().round(4)
    fautives = sommes[sommes != 1]
    if len(fautives):
        raise ValueError(f"{len(fautives)} blocs ne somment pas à 1 :\n{fautives.head()}")
