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
#
# Les réglages vivent dans l'objet REGLAGES. Les fonctions les lisent au moment
# de l'appel, jamais à l'import : on peut donc les changer depuis un notebook
# avec configurer(...), sans toucher à ce fichier.

from dataclasses import dataclass, field


@dataclass
class Reglages:
    seuil_effectif: int = 100               # séjours, sur la période d'observation (5 ans)
    part_min_service_habituel: float = 0.10 # part à partir de laquelle un service est de rang 1
    bornes_age: tuple = (17, 64, 74)        # âges révolus de fin des trois premières classes
    bornes_age_duree: tuple = (59,)         # idem pour les tables par GHM complet
    bornes_tranches_h: tuple = (1, 2, 4, 6, 12, 24)  # bornes des tranches horaires
    colonnes_site: tuple = ()               # colonnes ajoutées à tous les blocs, ex. ("hopital",) pour un calcul par site
    colonnes_saison: tuple = ()             # colonnes ajoutées aux blocs des tables saisonnières (venues, arrivées), ex. ("saison",)
    mois_debut_saison: int = 9              # la saison 2023-2024 va de septembre 2023 à août 2024
    situations_sans_sejour: tuple = ("consultation", "transfert", "deces", "parti")
    situations_avec_sejour: tuple = ("hospitalisation", "mono_uhcd")
    colonnes_venues: tuple = ("periode", "classe_age", "situation", "gravite", "racine_ghm")
    blocs_passage: dict = field(default_factory=lambda: {
        "complet": ["service", "precedent", "classe_age", "mode_entree"],
        "service": ["service"],
    })
    colonnes_contexte_passage: tuple = ("niveau", "service", "precedent", "classe_age", "mode_entree", "suite")
    parts_soins_critiques: tuple = ("part_rea", "part_usi", "part_usc")

    @property
    def classes_age(self) -> list[str]:
        return _libelles_classes(self.bornes_age)

    @property
    def classes_age_duree(self) -> list[str]:
        return _libelles_classes(self.bornes_age_duree)

    @property
    def tranches_h(self) -> list[str]:
        return _libelles_classes(self.bornes_tranches_h, premier=0, entier=False)


def _libelles_classes(bornes: tuple, premier: int = 0, entier: bool = True) -> list[str]:
    """Libellés « a-b » à partir des bornes : (17, 64, 74) → 0-17, 18-64, 65-74, 75+."""
    libelles, debut = [], premier
    for b in bornes:
        libelles.append(f"{debut}-{b}")
        debut = b + 1 if entier else b
    return libelles + [f"{debut}+"]


REGLAGES = Reglages()


def configurer(**valeurs) -> Reglages:
    """Modifie les réglages depuis un notebook : configurer(seuil_effectif=50).

    Renvoie l'objet REGLAGES, pour vérification. Un nom inconnu est refusé.
    """
    for nom, valeur in valeurs.items():
        if not hasattr(REGLAGES, nom):
            raise ValueError(f"réglage inconnu : {nom} (connus : {', '.join(vars(REGLAGES))})")
        setattr(REGLAGES, nom, valeur)
    return REGLAGES


_avertis: set = set()


def avec_site(colonnes: Sequence[str], df: pd.DataFrame | None = None,
              contexte: Sequence[str] | None = None) -> list[str]:
    """Colonnes de bloc complétées, en tête, par les colonnes de contexte.

    ``contexte`` donné (par exemple ["nohop"], ou [] pour aucun) : il est pris
    tel quel. Sinon : REGLAGES.colonnes_site, limité aux colonnes présentes dans
    ``df`` quand il est fourni, une colonne absente étant signalée une fois.
    """
    if contexte is not None:
        return [*contexte, *colonnes]
    cols_site = list(REGLAGES.colonnes_site)
    if df is not None:
        for c in cols_site:
            if c not in df.columns and c not in _avertis:
                _avertis.add(c)
                print(f"avertissement : calcul sans site, colonne {c} absente du tableau")
        cols_site = [c for c in cols_site if c in df.columns]
    return [*cols_site, *colonnes]


def avec_saison(colonnes: Sequence[str]) -> list[str]:
    """Colonnes de bloc complétées par les colonnes de saison (tables saisonnières seulement)."""
    return [*REGLAGES.colonnes_saison, *colonnes]


def saisons_completes(annees: Iterable[int]) -> list[str]:
    """Saisons entièrement couvertes par des années civiles : « 2020-2021 » si 2020 et 2021 y sont."""
    annees = set(annees)
    return [f"{a}-{a + 1}" for a in sorted(annees) if a + 1 in annees]


def saison(dates: pd.Series) -> pd.Series:
    """Libellé de saison d'une date, « 2023-2024 » pour septembre 2023 à août 2024
    (REGLAGES.mois_debut_saison fixe le mois de début)."""
    d = pd.to_datetime(dates)
    debut = d.dt.year.where(d.dt.month >= REGLAGES.mois_debut_saison, d.dt.year - 1)
    return debut.astype(str) + "-" + (debut + 1).astype(str)


def _classe(valeur: float, bornes: tuple, libelles: list[str]) -> str:
    for b, lib in zip(bornes, libelles):
        if valeur <= b:
            return lib
    return libelles[-1]


def classe_age(age: float) -> str:
    """Classe d'âge commune (âge révolu)."""
    return _classe(age, REGLAGES.bornes_age, REGLAGES.classes_age)


def classe_age_duree(age: float) -> str:
    """Classe d'âge des tables par GHM complet."""
    return _classe(age, REGLAGES.bornes_age_duree, REGLAGES.classes_age_duree)


def tranche_heures(duree_h: pd.Series) -> pd.Series:
    """Tranche horaire d'une durée en heures (borne basse incluse)."""
    bornes = [0, *REGLAGES.bornes_tranches_h, np.inf]
    return pd.cut(duree_h, bins=bornes, labels=REGLAGES.tranches_h, right=False, include_lowest=True).astype(str)


# ---------------------------------------------------------------------------
# Normalisation, rang, repli
# ---------------------------------------------------------------------------

def normaliser(df: pd.DataFrame, colonnes_bloc: Sequence[str], colonne_effectif: str = "n",
               seuil: int | None = None, contexte: Sequence[str] | None = None) -> pd.DataFrame:
    """Transforme des effectifs en parts par bloc, avec la règle des catégories rares.

    Les lignes sous le seuil d'un même bloc reçoivent chacune une part égale à
    leur masse totale divisée par leur nombre : le volume des cas rares est
    conservé sans qu'aucune proportion individuelle sous le seuil ne soit
    transmise. Renvoie les colonnes du bloc, les colonnes de catégorie,
    ``part`` et ``observe`` (True si la ligne était au-dessus du seuil).
    ``observe`` sert au calcul du rang et ne doit pas être écrite dans les
    fichiers. ``seuil`` : par défaut REGLAGES.seuil_effectif. Les colonnes de
    contexte sont prises telles quelles si ``contexte`` est donné ([] : aucune),
    sinon celles de REGLAGES présentes dans ``df``.
    """
    seuil = REGLAGES.seuil_effectif if seuil is None else seuil
    df = df.copy()
    colonnes_bloc = avec_site(colonnes_bloc, df, contexte)
    cles = [df[c] for c in colonnes_bloc]
    total = df.groupby(cles)[colonne_effectif].transform("sum")
    rare = df[colonne_effectif] < seuil
    df["observe"] = ~rare
    masse_rare = df[colonne_effectif].where(rare, 0).groupby(cles).transform("sum")
    nb_rare = rare.groupby(cles).transform("sum")
    df["part"] = np.where(rare, masse_rare / nb_rare.replace(0, np.nan), df[colonne_effectif]) / total
    return df.drop(columns=[colonne_effectif]).round({"part": 6})


def attribuer_rang(t: pd.DataFrame, part_min: float | None = None,
                   masque: pd.Series | None = None) -> pd.Series:
    """Rang des services : 1 habituel (part >= part_min), 2 exceptionnel (observé
    mais sous part_min), 3 complété par la règle des catégories rares.

    ``masque`` : lignes auxquelles le rang s'applique (les autres reçoivent une
    case vide), par exemple les suites qui sont des services et non « sortie ».
    """
    part_min = REGLAGES.part_min_service_habituel if part_min is None else part_min
    if masque is None:
        masque = pd.Series(True, index=t.index)
    rang = np.select([~masque, ~t["observe"], t["part"] >= part_min], [np.nan, 3, 1], default=2)
    return pd.Series(rang, index=t.index).astype("Int64")


def table_proportions(effectifs: pd.DataFrame, colonnes_bloc: Sequence[str],
                      colonnes_categorie: Sequence[str],
                      replis: Iterable[Sequence[str]] = (),
                      seuil: int | None = None, contexte: Sequence[str] | None = None) -> pd.DataFrame:
    """Table de proportions avec ses lignes de repli.

    ``colonnes_bloc`` : contexte du niveau le plus fin ; ``colonnes_categorie`` :
    ce qui est tiré ; ``replis`` : liste de sous-ensembles de ``colonnes_bloc``,
    du plus détaillé au plus général. Pour chaque repli, les effectifs sont
    sommés sur les colonnes absentes, normalisés, et les colonnes absentes
    sont laissées vides. Renvoie les colonnes du bloc, les catégories et
    ``part`` (sans ``observe``).
    """
    colonnes_bloc = avec_site(colonnes_bloc, effectifs, contexte)
    colonnes_categorie = list(colonnes_categorie)
    morceaux = [normaliser(effectifs, colonnes_bloc, seuil=seuil, contexte=[])]
    for sous_bloc in replis:
        sous_bloc = avec_site(sous_bloc, effectifs, contexte)
        eff = effectifs.groupby(sous_bloc + colonnes_categorie, as_index=False)["n"].sum()
        morceaux.append(normaliser(eff, sous_bloc, seuil=seuil, contexte=[]))
    colonnes = colonnes_bloc + colonnes_categorie + ["part"]
    return pd.concat(morceaux, ignore_index=True).reindex(columns=colonnes).fillna({c: "" for c in colonnes_bloc})


def additionner(effectifs_par_annee: Iterable[pd.DataFrame], colonnes: Sequence[str]) -> pd.DataFrame:
    """Somme des effectifs de plusieurs années (mêmes colonnes de contexte + ``n``)."""
    tout = pd.concat(list(effectifs_par_annee), ignore_index=True)
    return tout.groupby(avec_site(colonnes, tout), as_index=False)["n"].sum()


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
    situations avec séjour. Si REGLAGES.colonnes_saison est renseigné, ``v`` doit
    porter la colonne correspondante (voir ``saison``). Sont exclus, et comptés : les venues de devenir
    ``inconnu`` (admission sur place sans séjour retrouvé) et les séjours
    chaînés sans GHM (non groupés).
    """
    v = v.copy()
    v["situation"] = situation_venue(v)
    inconnu = v["situation"].eq("inconnu")
    if inconnu.any():
        print(f"{inconnu.mean():.1%} de venues au devenir inconnu (admission codée sans séjour retrouvé), exclues")
    v = v[~inconnu]
    avec_sejour = v["situation"].isin(REGLAGES.situations_avec_sejour)
    v["gravite"] = np.where(avec_sejour, "", gravite_ccmu(v["ccmu"]))
    v["racine_ghm"] = np.where(avec_sejour, v["ghm"].astype(str).str[:5], "")
    v = v[~(avec_sejour & v["ghm"].isna())]
    return v.groupby(avec_site(avec_saison(REGLAGES.colonnes_venues), v)).size().reset_index(name="n")


def venues_depuis_effectifs(effectifs: pd.DataFrame, seuil: int | None = None, contexte: Sequence[str] | None = None) -> pd.DataFrame:
    """venues_urgences.csv : bloc saison × mois × classe d'âge, repli saison × classe d'âge."""
    return table_proportions(effectifs, avec_saison(["periode", "classe_age"]), ["situation", "gravite", "racine_ghm"],
                             replis=[avec_saison(["classe_age"])], seuil=seuil, contexte=contexte)


# ---------------------------------------------------------------------------
# Profils (profil_arrivees_urgences.csv, profil_entrees_programmees.csv, profil_sorties.csv…)
# ---------------------------------------------------------------------------

def effectifs_profil(df: pd.DataFrame, dimensions: dict[str, pd.Series]) -> pd.DataFrame:
    """Effectifs par dimension : une ligne par (dimension, valeur), avec ``n``.

    ``dimensions`` : nom → série de valeurs alignée sur ``df`` (par exemple
    {"heure": d.dt.hour, "mois": d.dt.month}).
    """
    morceaux = []
    for dimension, valeur in dimensions.items():
        t = df.assign(dimension=dimension, valeur=np.asarray(valeur))
        morceaux.append(t.groupby(avec_site(avec_saison(["dimension", "valeur"]), t)).size().reset_index(name="n"))
    return pd.concat(morceaux, ignore_index=True)


COLONNES_PROFIL_ARRIVEES = ["dimension", "jour_semaine", "heure", "mois"]


def effectifs_arrivees(v: pd.DataFrame) -> pd.DataFrame:
    """Effectifs des arrivées aux urgences en deux composantes : ``semaine``
    (jour de la semaine × heure, 1 = lundi … 7 = dimanche) et ``mois``.
    Colonne attendue : ``date_entree`` (date et heure)."""
    d = pd.to_datetime(v["date_entree"])
    contexte = avec_site(avec_saison([]), v)
    base = v[contexte].copy()
    semaine = base.assign(dimension="semaine", jour_semaine=(d.dt.dayofweek + 1).values, heure=d.dt.hour.values)
    mois = base.assign(dimension="mois", mois=d.dt.month.values)
    morceaux = [
        semaine.groupby(contexte + ["dimension", "jour_semaine", "heure"]).size().reset_index(name="n"),
        mois.groupby(contexte + ["dimension", "mois"]).size().reset_index(name="n"),
    ]
    return (pd.concat(morceaux, ignore_index=True)
            .reindex(columns=contexte + COLONNES_PROFIL_ARRIVEES + ["n"]).fillna(""))


def profil_depuis_effectifs(effectifs: pd.DataFrame) -> pd.DataFrame:
    """Parts par dimension (elles somment à 1 dans chaque dimension), sans seuil."""
    t = effectifs.copy()
    cles = avec_site(avec_saison(["dimension"]), t)
    t["part"] = (t["n"] / t.groupby(cles)["n"].transform("sum")).round(6)
    tri = cles + [c for c in ("valeur", "jour_semaine", "heure", "mois") if c in t.columns]
    return t.drop(columns="n").sort_values(tri).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Profil des patients des urgences (profil_patients_urgences.csv)
# ---------------------------------------------------------------------------

def sexe_mf(sexe: pd.Series) -> pd.Series:
    """Sexe en 'M' / 'F' quels que soient les codes (1/2, M/F, H/F) ; inconnu → ''."""
    s = sexe.astype(str).str.strip().str.upper().replace({"1": "M", "2": "F", "H": "M"})
    return s.where(s.isin(["M", "F"]), "")


def effectifs_patients_urgences(v: pd.DataFrame) -> pd.DataFrame:
    """Effectifs des venues par classe d'âge × sexe (sexe inconnu écarté).
    Colonnes attendues : ``classe_age``, ``sexe``."""
    t = v.assign(sexe=sexe_mf(v["sexe"]))
    t = t[t["sexe"] != ""]
    return t.groupby(avec_site(["classe_age", "sexe"], t)).size().reset_index(name="n")


def patients_urgences_depuis_effectifs(effectifs: pd.DataFrame) -> pd.DataFrame:
    """profil_patients_urgences.csv : parts qui somment à 1 sur l'ensemble (par site)."""
    t = effectifs.copy()
    cles = avec_site([], t)
    total = t.groupby(cles)["n"].transform("sum") if cles else t["n"].sum()
    t["part"] = (t["n"] / total).round(6)
    return t.drop(columns="n").sort_values(cles + ["classe_age", "sexe"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Gravité CCMU des hospitalisés (gravite_urgences.csv)
# ---------------------------------------------------------------------------

def effectifs_gravite(v: pd.DataFrame) -> pd.DataFrame:
    """Effectifs des venues hospitalisées par classe d'âge × situation × tranche
    de durée × gravité (CCMU inconnue écartée). Colonnes attendues :
    ``classe_age``, ``ccmu``, ``date_entree``, ``date_sortie`` et celles de
    ``situation_venue``."""
    t = v.assign(situation=situation_venue(v))
    t = t[t["situation"].isin(REGLAGES.situations_avec_sejour)].copy()
    t["gravite"] = gravite_ccmu(t["ccmu"])
    t = t[t["gravite"] != ""]
    duree_h = (pd.to_datetime(t["date_sortie"]) - pd.to_datetime(t["date_entree"])).dt.total_seconds() / 3600
    t["tranche_h"] = tranche_heures(duree_h)
    return t.groupby(avec_site(["classe_age", "situation", "tranche_h", "gravite"], t)).size().reset_index(name="n")


def gravite_depuis_effectifs(effectifs: pd.DataFrame, seuil: int | None = None, contexte: Sequence[str] | None = None) -> pd.DataFrame:
    """gravite_urgences.csv : bloc classe_age × situation × tranche_h, repli classe_age × situation."""
    return table_proportions(effectifs, ["classe_age", "situation", "tranche_h"], ["gravite"],
                             replis=[["classe_age", "situation"]], seuil=seuil, contexte=contexte)


# ---------------------------------------------------------------------------
# Premier service par racine (premier_service_racine.csv)
# ---------------------------------------------------------------------------

def premier_service_depuis_effectifs(effectifs: pd.DataFrame, seuil: int | None = None,
                                     part_min: float | None = None, contexte: Sequence[str] | None = None) -> pd.DataFrame:
    """premier_service_racine.csv à partir d'effectifs (racine_ghm, classe_age,
    specialite, n) : parts, rang, et lignes de repli par racine seule."""
    morceaux = []
    for bloc in (["racine_ghm", "classe_age"], ["racine_ghm"]):
        bloc = avec_site(bloc, effectifs, contexte)
        eff = effectifs.groupby(bloc + ["specialite"], as_index=False)["n"].sum()
        t = normaliser(eff, bloc, seuil=seuil, contexte=[])
        t["rang"] = attribuer_rang(t, part_min=part_min)
        morceaux.append(t)
    colonnes = avec_site(["racine_ghm", "classe_age", "specialite", "part", "rang"], effectifs, contexte)
    return pd.concat(morceaux, ignore_index=True).reindex(columns=colonnes).fillna({"classe_age": ""})


# ---------------------------------------------------------------------------
# Table de passage (table_passage.csv)
# ---------------------------------------------------------------------------

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
        avec_site(["id_sejour", "service", "precedent", "classe_age", "mode_entree", "suite"], df)]


def effectifs_passage(etapes: pd.DataFrame) -> pd.DataFrame:
    """Compte les transitions aux deux niveaux. Colonnes : contexte + ``n``."""
    morceaux = []
    for niveau, colonnes in REGLAGES.blocs_passage.items():
        n = etapes.groupby(avec_site(colonnes, etapes) + ["suite"]).size().reset_index(name="n")
        n["niveau"] = niveau
        morceaux.append(n)
    return (pd.concat(morceaux, ignore_index=True)
            .reindex(columns=avec_site(REGLAGES.colonnes_contexte_passage, etapes) + ["n"]).fillna(""))


def table_passage_depuis_effectifs(effectifs: pd.DataFrame, seuil: int | None = None,
                                   part_min: float | None = None, contexte: Sequence[str] | None = None) -> pd.DataFrame:
    """table_passage.csv : normalisation par niveau et rang des suites."""
    morceaux = []
    for niveau, colonnes in REGLAGES.blocs_passage.items():
        e = effectifs[effectifs["niveau"] == niveau]
        t = normaliser(e, colonnes, seuil=seuil, contexte=contexte)
        t["rang"] = attribuer_rang(t, part_min=part_min, masque=t["suite"].ne("sortie"))
        morceaux.append(t)
    return (pd.concat(morceaux, ignore_index=True)
            .reindex(columns=avec_site(REGLAGES.colonnes_contexte_passage, effectifs, contexte) + ["part", "rang"]))


# ---------------------------------------------------------------------------
# Durées de passage par spécialité (durees_passage.csv)
# ---------------------------------------------------------------------------

def passages_specialite(rum: pd.DataFrame) -> pd.DataFrame:
    """Une ligne par passage de spécialité (RUM consécutifs de même spécialité
    fusionnés), avec sa durée en jours décimaux.

    Colonnes attendues : ``id_sejour``, ``rang``, ``specialite`` (UHCD déjà
    retirée), ``date_entree``, ``date_sortie`` (datetime), ``mode_entree``,
    ``classe_age``.
    """
    df = rum.sort_values(["id_sejour", "rang"]).reset_index(drop=True)
    for c in ("date_entree", "date_sortie"):          # dates sans heure ou en texte selon le pilote : on force le type
        df[c] = pd.to_datetime(df[c])
    nouveau = ~(df["id_sejour"].eq(df["id_sejour"].shift()) & df["specialite"].eq(df["specialite"].shift()))
    df["id_passage"] = nouveau.cumsum()
    premieres = {c: (c, "first") for c in avec_site(["specialite", "mode_entree", "classe_age"], df)}
    p = df.groupby("id_passage").agg(**premieres, date_entree=("date_entree", "min"), date_sortie=("date_sortie", "max"))
    p["duree_j"] = (p["date_sortie"] - p["date_entree"]).dt.total_seconds() / 86400
    return p[avec_site(["specialite", "mode_entree", "classe_age", "duree_j"], df)].reset_index(drop=True)


def durees_passage_depuis_passages(passages: pd.DataFrame, seuil: int | None = None, contexte: Sequence[str] | None = None) -> pd.DataFrame:
    """durees_passage.csv : médiane, quartiles et moyenne par spécialité × mode
    d'entrée × classe d'âge (blocs d'au moins ``seuil`` passages), plus le
    repli par spécialité seule."""
    seuil = REGLAGES.seuil_effectif if seuil is None else seuil

    def resume(bloc: list[str]) -> pd.DataFrame:
        g = passages.groupby(avec_site(bloc, passages, contexte))["duree_j"]
        q = g.quantile([0.25, 0.5, 0.75]).unstack()      # vectoriel : tous les blocs d'un coup
        q.columns = ["q1_j", "mediane_j", "q3_j"]
        t = pd.concat([g.size().rename("n"), g.mean().rename("moyenne_j"), q], axis=1).reset_index()
        return t[t["n"] >= seuil].drop(columns="n")
    fin = resume(["specialite", "mode_entree", "classe_age"])
    repli = resume(["specialite"])
    colonnes = avec_site(["specialite", "mode_entree", "classe_age", "mediane_j", "q1_j", "q3_j", "moyenne_j"], passages, contexte)
    return (pd.concat([fin, repli], ignore_index=True).reindex(columns=colonnes)
            .fillna({"mode_entree": "", "classe_age": ""}).round(3))


# ---------------------------------------------------------------------------
# Soins critiques par GHM (soins_critiques_ghm.csv)
# ---------------------------------------------------------------------------

def soins_critiques_depuis_effectifs(fin: pd.DataFrame, ghm: pd.DataFrame, racine: pd.DataFrame,
                                     seuil: int | None = None, contexte: Sequence[str] | None = None) -> pd.DataFrame:
    """soins_critiques_ghm.csv avec le repli appliqué à la fabrication.

    ``fin`` : (ghm, classe_age_duree, n, part_rea, part_usi, part_usc) ;
    ``ghm`` : idem par GHM seul ; ``racine`` : (racine_ghm, part_*). Une case
    sous le seuil prend les parts du GHM seul, puis de la racine.
    """
    seuil = REGLAGES.seuil_effectif if seuil is None else seuil
    parts = list(REGLAGES.parts_soins_critiques)
    site = avec_site([], fin, contexte)
    t = fin.merge(ghm[site + ["ghm", "n"] + parts], on=site + ["ghm"], suffixes=("", "_ghm"))
    for p in parts:
        t[p] = t[p].where(t["n"] >= seuil, t[p + "_ghm"])
    t["racine_ghm"] = t["ghm"].str[:5]
    t = t.merge(racine[site + ["racine_ghm"] + parts], on=site + ["racine_ghm"], suffixes=("", "_racine"))
    for p in parts:
        t[p] = t[p].where(t["n_ghm"] >= seuil, t[p + "_racine"])
    return t[site + ["ghm", "classe_age_duree"] + parts].round(6)


# ---------------------------------------------------------------------------
# Contrôles
# ---------------------------------------------------------------------------

def verifier_table(t: pd.DataFrame, colonnes_bloc: Sequence[str]) -> None:
    """Vérifie qu'une table de proportions somme à 1 dans chaque bloc."""
    sommes = t.groupby(avec_site(colonnes_bloc, t))["part"].sum().round(4)
    fautives = sommes[sommes != 1]
    if len(fautives):
        raise ValueError(f"{len(fautives)} blocs ne somment pas à 1 :\n{fautives.head()}")
