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

from pathlib import Path
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
    seuil_effectif: int = 25                # fiabilité d'un bloc : en dessous, le bloc n'est pas écrit (le repli sert)
    seuil_lissage: int = 20                 # lissage des parts : les lignes de moins de n séjours mettent leur masse en commun
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


def avec_saison(colonnes: Sequence[str], df: pd.DataFrame | None = None) -> list[str]:
    """Colonnes de bloc complétées par les colonnes de saison (tables saisonnières seulement).
    Avec ``df`` : seulement les colonnes de saison présentes dans le tableau (un
    profil d'entrées programmées ou de sorties n'a pas de saison)."""
    cols = list(REGLAGES.colonnes_saison)
    if df is not None:
        cols = [c for c in cols if c in df.columns]
    return [*cols, *colonnes]


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

def normaliser(df: pd.DataFrame, colonnes_bloc: Sequence[str], colonnes_categorie: Sequence[str] | None = None,
               colonne_effectif: str = "n", seuil: int | None = None, contexte: Sequence[str] | None = None,
               seuil_lissage: int | None = None, colonnes_lissage: Sequence[str] = ()) -> pd.DataFrame:
    """Transforme des effectifs en parts par bloc, avec la règle des catégories rares.

    ``colonnes_lissage`` : colonnes de catégorie à l'intérieur desquelles les petites lignes
    mettent leur masse en commun. Sans elle, toutes les petites lignes d'un bloc sont mises
    ensemble ; or quand les catégories sont composées (situation × gravité × racine), cela
    déplace de la masse d'une situation vers une autre (les nombreuses petites lignes
    « hospitalisation × racine » absorberaient celles des consultations rares). Avec
    ``colonnes_lissage=["situation"]``, le lissage reste à l'intérieur de chaque situation.

    Les lignes sous le seuil d'un même bloc reçoivent chacune une part égale à
    leur masse totale divisée par leur nombre : le volume des cas rares est
    conservé sans qu'aucune proportion individuelle sous le seuil ne soit
    transmise. Renvoie les colonnes du bloc, les colonnes de catégorie,
    ``part`` et ``observe`` (True si la ligne n'a pas été lissée).

    Deux seuils. ``seuil`` (REGLAGES.seuil_effectif) juge la fiabilité du
    **bloc** : un bloc de moins de ``seuil`` séjours n'est pas écrit, le
    simulateur utilisera son repli. ``seuil_lissage`` (REGLAGES.seuil_lissage)
    lisse les **parts** à l'intérieur d'un bloc gardé : les lignes de moins de
    ``seuil_lissage`` séjours mettent leur masse en commun et la partagent à
    égalité. ``observe`` ne doit pas être écrite dans les
    fichiers. ``colonnes_categorie`` : ce qui est tiré ; si elle est donnée, la
    sortie se limite à bloc + catégories + part + observe, dans cet ordre ;
    sinon toute colonne qui n'est ni le bloc ni l'effectif est une catégorie.
    ``seuil`` : par défaut REGLAGES.seuil_effectif. Les colonnes de contexte
    sont prises telles quelles si ``contexte`` est donné ([] : aucune), sinon
    celles de REGLAGES présentes dans ``df``.
    """
    seuil = REGLAGES.seuil_effectif if seuil is None else seuil
    seuil_lissage = REGLAGES.seuil_lissage if seuil_lissage is None else seuil_lissage
    df = df.copy()
    colonnes_bloc = avec_site(colonnes_bloc, df, contexte)
    bloc_vide = not colonnes_bloc                 # bloc vide : les parts somment à 1 sur l'ensemble
    if bloc_vide:
        df["_bloc"] = 0
        colonnes_bloc = ["_bloc"]
    if colonnes_categorie is not None:
        df = df[colonnes_bloc + list(colonnes_categorie) + [colonne_effectif]]
        df = df.groupby(colonnes_bloc + list(colonnes_categorie), as_index=False)[colonne_effectif].sum()
    cles = [df[c] for c in colonnes_bloc]
    total = df.groupby(cles)[colonne_effectif].transform("sum")
    fiable = total >= seuil                       # fiabilité du bloc : les blocs trop petits sortent de la table
    df, total = df[fiable], total[fiable]
    cles = [df[c] for c in colonnes_bloc]
    rare = df[colonne_effectif] < seuil_lissage   # lissage des parts, ligne par ligne, dans les blocs gardés
    df["observe"] = ~rare
    cles_lissage = cles + [df[c] for c in colonnes_lissage if c in df.columns]
    masse_rare = df[colonne_effectif].where(rare, 0).groupby(cles_lissage).transform("sum")
    nb_rare = rare.groupby(cles_lissage).transform("sum")
    df["part"] = np.where(rare, masse_rare / nb_rare.replace(0, np.nan), df[colonne_effectif]) / total
    if bloc_vide:
        df = df.drop(columns=["_bloc"])
    return df.drop(columns=[colonne_effectif]).round({"part": 6})


def attribuer_rang(t: pd.DataFrame, part_min: float | None = None,
                   masque: pd.Series | None = None) -> pd.Series:
    """Rang des services : 1 habituel (part >= part_min), 2 exceptionnel (part
    sous part_min). Toute ligne présente a été observée : il n'y a pas de rang 3.

    ``masque`` : lignes auxquelles le rang s'applique (les autres reçoivent une
    case vide), par exemple les suites qui sont des services et non « sortie ».
    """
    part_min = REGLAGES.part_min_service_habituel if part_min is None else part_min
    if masque is None:
        masque = pd.Series(True, index=t.index)
    rang = np.select([~masque, t["part"] >= part_min], [np.nan, 1], default=2)
    return pd.Series(rang, index=t.index).astype("Int64")


def restreindre_services(table: pd.DataFrame, services: Iterable[str], colonnes_bloc: Sequence[str],
                         colonne_service: str = "specialite", part_min: float | None = None) -> pd.DataFrame:
    """Ce que fait le simulateur au chargement d'une table de pratiques calculée
    sur plusieurs sites : ne garder que les services présents dans l'hôpital,
    renormaliser les parts dans chaque bloc et recalculer les rangs (un service
    exceptionnel ailleurs peut être le service habituel d'un hôpital qui n'a pas
    la spécialité principale). Les blocs où aucun service ne subsiste disparaissent
    (les patients concernés sont transférés, règle D)."""
    t = table[table[colonne_service].isin(set(services))].copy()
    cles = [t[c].astype(str) for c in colonnes_bloc]
    t["part"] = t["part"] / t.groupby(cles)["part"].transform("sum")
    t["rang"] = attribuer_rang(t, part_min=part_min)
    return t


def table_proportions(effectifs: pd.DataFrame, colonnes_bloc: Sequence[str],
                      colonnes_categorie: Sequence[str],
                      replis: Iterable[Sequence[str]] = (),
                      seuil: int | None = None, contexte: Sequence[str] | None = None,
                      colonnes_lissage: Sequence[str] = (), seuil_lissage: int | None = None) -> pd.DataFrame:
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
    morceaux = [normaliser(effectifs, colonnes_bloc, colonnes_categorie, seuil=seuil, contexte=[], colonnes_lissage=colonnes_lissage, seuil_lissage=seuil_lissage)]
    for sous_bloc in replis:
        sous_bloc = avec_site(sous_bloc, effectifs, contexte)
        morceaux.append(normaliser(effectifs, sous_bloc, colonnes_categorie, seuil=seuil, contexte=[], colonnes_lissage=colonnes_lissage, seuil_lissage=seuil_lissage))
    colonnes = colonnes_bloc + colonnes_categorie + ["part"]
    # les colonnes de bloc passent en texte avant la concaténation : les cases vides des replis
    # ne transforment pas un mois « 1 » en « 1.0 »
    for m in morceaux:
        for c in colonnes_bloc:
            if c in m.columns:
                m[c] = m[c].astype(str)
    return pd.concat(morceaux, ignore_index=True).reindex(columns=colonnes).fillna({c: "" for c in colonnes_bloc})


def additionner(effectifs_par_annee: Iterable[pd.DataFrame], colonnes: Sequence[str]) -> pd.DataFrame:
    """Somme des effectifs de plusieurs années (mêmes colonnes de contexte + ``n``).
    Accepte aussi un tableau seul."""
    if isinstance(effectifs_par_annee, pd.DataFrame):
        effectifs_par_annee = [effectifs_par_annee]
    tout = pd.concat(list(effectifs_par_annee), ignore_index=True)
    return tout.groupby(avec_site(colonnes, tout), as_index=False)["n"].sum()


# ---------------------------------------------------------------------------
# Réglages locaux (chemins propres à la machine, hors du dépôt)
# ---------------------------------------------------------------------------

NOM_CONFIG_LOCALE = "config_locale.yaml"


def racine_depot(depart: str | Path | None = None) -> Path:
    """Le dossier du dépôt : le premier, en remontant depuis ``depart`` (le dossier courant
    par défaut), qui contient ``gen_admissions/`` ; à défaut, le dossier qui contient ce module."""
    depart = Path(depart or Path.cwd()).resolve()
    for p in [depart, *depart.parents]:
        if (p / "gen_admissions").is_dir():
            return p
    return Path(__file__).resolve().parents[1]


def config_locale(fichier: str | Path | None = NOM_CONFIG_LOCALE, depart: str | Path | None = None) -> dict:
    """Les réglages propres à la machine, lus dans ``config_locale.yaml`` à la racine du dépôt
    (fichier non suivi par git ; modèle : ``config_locale.exemple.yaml``). Sans fichier, les
    valeurs par défaut du dépôt : l'hôpital fictif, un dossier ``data`` à la racine, pas de site.

    ``fichier`` : le fichier de réglages à lire (chemin absolu, ou relatif à la racine du dépôt) ;
    ``None`` ignore tout fichier et rend les valeurs par défaut — c'est le moyen de travailler sur
    l'hôpital fictif alors qu'un ``config_locale.yaml`` pointe vers les données réelles :
    ``config_locale(None)``. ``depart`` : d'où remonter pour trouver la racine du dépôt (dossier courant).

    Clés : ``dossier_parametres`` (les 22 fichiers), ``dossier_data`` (requêtes mémorisées en
    parquet), ``site`` (le site exporté, ou None), ``fichier_tension`` (série quotidienne,
    facultative). Les chemins relatifs sont pris depuis la racine du dépôt ; ``racine`` est ajoutée.
    """
    import yaml

    racine = racine_depot(depart)
    valeurs = {"dossier_parametres": "parametres/hopital_type", "dossier_data": "data", "site": None, "fichier_tension": None}
    if fichier is not None:
        chemin = Path(fichier) if Path(fichier).is_absolute() else racine / fichier
        if chemin.exists():
            with open(chemin, encoding="utf-8") as f:
                valeurs.update({k: v for k, v in (yaml.safe_load(f) or {}).items() if v is not None})
        elif str(fichier) != NOM_CONFIG_LOCALE:
            raise FileNotFoundError(f"fichier de réglages introuvable : {chemin}")
    for cle in ("dossier_parametres", "dossier_data", "fichier_tension"):
        if valeurs[cle] is not None:
            valeurs[cle] = (racine / Path(valeurs[cle])).resolve() if not Path(valeurs[cle]).is_absolute() else Path(valeurs[cle])
    if valeurs["site"] is not None:
        valeurs["site"] = str(valeurs["site"])
    valeurs["racine"] = racine
    return valeurs


def dossier_simulation(dossier_parametres: str | Path, nom: str, racine: str | Path | None = None) -> Path:
    """Où écrire les sorties d'une simulation nommée ``nom`` faite sur ce dossier de paramètres.

    Règle : ``<dossier_parametres>/simulations/<nom>`` — donc dans ``prod/`` pour des paramètres réels,
    hors dépôt — sauf pour un dossier du dépôt lui-même (``parametres/…``, l'hôpital fictif), où l'on
    écrit dans ``<racine>/sorties/<nom>`` pour ne pas mêler sorties et paramètres versionnés.
    """
    dossier = Path(dossier_parametres).resolve()
    racine = racine_depot(racine)
    if (racine / "parametres") in dossier.parents or dossier == racine / "parametres":
        return racine / "sorties" / nom
    return dossier / "simulations" / nom


# ---------------------------------------------------------------------------
# Mémorisation des résultats de requête (fichiers parquet)
# ---------------------------------------------------------------------------

def memoriser(nom: str, calcul, dossier: str | Path = "data", forcer: bool = False) -> pd.DataFrame:
    """Résultat de ``calcul()`` (un DataFrame), lu dans ``dossier/nom.parquet`` si ce
    fichier existe, sinon calculé puis enregistré là. ``forcer=True`` recalcule
    même si le fichier existe (après un changement de requête, par exemple).

    Sert à ne pas relancer les requêtes longues à chaque ouverture du notebook :
        v = memoriser("venues_25", lambda: con.read_sql(requete), DOSSIER_DATA)
    """
    chemin = Path(dossier) / f"{nom}.parquet"
    if chemin.exists() and not forcer:
        df = pd.read_parquet(chemin)
        print(f"{nom} : {len(df)} lignes lues dans {chemin}")
        return df
    df = calcul()
    chemin.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(chemin, index=False)
    print(f"{nom} : {len(df)} lignes calculées et enregistrées dans {chemin}")
    return df


def lire_sql(nom: str, requete: str, connexion, dossier: str | Path = "data", forcer: bool = False) -> pd.DataFrame:
    """``connexion.read_sql(requete)`` mémorisé dans ``dossier/nom.parquet`` (voir ``memoriser``)."""
    return memoriser(nom, lambda: connexion.read_sql(requete), dossier, forcer)


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
    entiers = {"lits", "lits_ouverts", "age_min", "age_max"}
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


def effectifs_venues(v: pd.DataFrame, contexte: Sequence[str] | None = None) -> pd.DataFrame:
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
    # séjours sans GHM ou en CMD 90 (erreur de groupage) : pas de niveau ni de durée possibles, exclus
    erreur = avec_sejour & (v["ghm"].isna() | v["ghm"].astype(str).str.startswith("90"))
    if erreur.any():
        print(f"{erreur.sum()} venues hospitalisées sans GHM exploitable (absent ou CMD 90), exclues")
    v = v[~erreur]
    return v.groupby(avec_site(avec_saison(REGLAGES.colonnes_venues), v, contexte)).size().reset_index(name="n")


def venues_depuis_effectifs(effectifs: pd.DataFrame, seuil: int | None = None, contexte: Sequence[str] | None = None) -> pd.DataFrame:
    """venues_urgences.csv : bloc saison × mois × classe d'âge, repli saison × classe d'âge."""
    return table_proportions(effectifs, avec_saison(["periode", "classe_age"]), ["situation", "gravite", "racine_ghm"],
                             replis=[avec_saison(["classe_age"])], seuil=seuil, contexte=contexte,
                             colonnes_lissage=["situation"])     # le lissage ne déplace pas de masse entre situations


# ---------------------------------------------------------------------------
# Profils (profil_arrivees_urgences.csv, profil_entrees_programmees.csv, profil_sorties.csv…)
# ---------------------------------------------------------------------------

def effectifs_profil(df: pd.DataFrame, dimensions: dict[str, pd.Series],
                     contexte: Sequence[str] | None = None) -> pd.DataFrame:
    """Effectifs par dimension : une ligne par (dimension, valeur), avec ``n``.

    ``dimensions`` : nom → série de valeurs alignée sur ``df`` (par exemple
    {"heure": d.dt.hour, "mois": d.dt.month}).
    """
    morceaux = []
    for dimension, valeur in dimensions.items():
        t = df.assign(dimension=dimension, valeur=np.asarray(valeur))
        morceaux.append(t.groupby(avec_site(avec_saison(["dimension", "valeur"], t), t, contexte)).size().reset_index(name="n"))
    return pd.concat(morceaux, ignore_index=True)


COLONNES_PROFIL_ARRIVEES = ["dimension", "jour_semaine", "heure", "mois"]


def effectifs_arrivees(v: pd.DataFrame, contexte: Sequence[str] | None = None) -> pd.DataFrame:
    """Effectifs des arrivées aux urgences en deux composantes : ``semaine``
    (jour de la semaine × heure, 1 = lundi … 7 = dimanche) et ``mois``.
    Colonne attendue : ``date_entree`` (date et heure)."""
    d = pd.to_datetime(v["date_entree"])
    contexte = avec_site(avec_saison([], v), v, contexte)
    base = v[contexte].copy()
    semaine = base.assign(dimension="semaine", jour_semaine=(d.dt.dayofweek + 1).values, heure=d.dt.hour.values)
    mois = base.assign(dimension="mois", mois=d.dt.month.values)
    morceaux = [
        semaine.groupby(contexte + ["dimension", "jour_semaine", "heure"]).size().reset_index(name="n"),
        mois.groupby(contexte + ["dimension", "mois"]).size().reset_index(name="n"),
    ]
    t = pd.concat(morceaux, ignore_index=True).reindex(columns=contexte + COLONNES_PROFIL_ARRIVEES + ["n"])
    for c in ("jour_semaine", "heure", "mois"):          # entiers en texte, vides sur l'autre dimension (pas de « 1.0 »)
        t[c] = t[c].map(lambda x: "" if pd.isna(x) else str(int(x)))
    return t


def effectifs_profil_semaine(df: pd.DataFrame, dates: pd.Series,
                             contexte: Sequence[str] | None = None) -> pd.DataFrame:
    """Effectifs par jour de la semaine (1 = lundi … 7 = dimanche) × heure, en une
    dimension ``semaine`` : la forme des profils d'entrées programmées et de
    sorties (sur les mouvements) et de la composante semaine des arrivées.
    ``dates`` : série de dates et heures alignée sur ``df``."""
    d = pd.to_datetime(dates)
    cles = avec_site(avec_saison([], df), df, contexte)
    t = df[cles].assign(dimension="semaine", jour_semaine=(d.dt.dayofweek + 1).values, heure=d.dt.hour.values)
    return t.groupby(cles + ["dimension", "jour_semaine", "heure"]).size().reset_index(name="n")


def effectifs_entrees_programmees(sejours: pd.DataFrame, contexte: Sequence[str] | None = None) -> pd.DataFrame:
    """Effectifs des entrées programmées (séjours non venus des urgences) par jour
    de la semaine (1 = lundi) et, si la date d'entrée porte une heure, par heure.
    Colonne attendue : ``date_entree``."""
    d = pd.to_datetime(sejours["date_entree"])
    dimensions = {"jour_semaine": d.dt.dayofweek + 1}
    if (d.dt.hour != 0).any():
        dimensions["heure"] = d.dt.hour
    return effectifs_profil(sejours, dimensions, contexte)


def profil_depuis_effectifs(effectifs: pd.DataFrame, contexte: Sequence[str] | None = None) -> pd.DataFrame:
    """Parts par dimension (elles somment à 1 dans chaque dimension), sans seuil."""
    t = effectifs.copy()
    cles = avec_site(avec_saison(["dimension"], t), t, contexte)
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


def effectifs_patients_urgences(v: pd.DataFrame, contexte: Sequence[str] | None = None) -> pd.DataFrame:
    """Effectifs des venues par classe d'âge × sexe (sexe inconnu écarté).
    Colonnes attendues : ``classe_age``, ``sexe``."""
    t = v.assign(sexe=sexe_mf(v["sexe"]))
    t = t[t["sexe"] != ""]
    return t.groupby(avec_site(["classe_age", "sexe"], t, contexte)).size().reset_index(name="n")


def patients_urgences_depuis_effectifs(effectifs: pd.DataFrame, contexte: Sequence[str] | None = None) -> pd.DataFrame:
    """profil_patients_urgences.csv : parts qui somment à 1 sur l'ensemble (par site)."""
    t = effectifs.copy()
    cles = avec_site([], t, contexte)
    total = t.groupby(cles)["n"].transform("sum") if cles else t["n"].sum()
    t["part"] = (t["n"] / total).round(6)
    return t.drop(columns="n").sort_values(cles + ["classe_age", "sexe"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Gravité CCMU des hospitalisés (gravite_urgences.csv)
# ---------------------------------------------------------------------------

def effectifs_gravite(v: pd.DataFrame, contexte: Sequence[str] | None = None) -> pd.DataFrame:
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
    return t.groupby(avec_site(["classe_age", "situation", "tranche_h", "gravite"], t, contexte)).size().reset_index(name="n")


def gravite_depuis_effectifs(effectifs: pd.DataFrame, seuil: int | None = None, contexte: Sequence[str] | None = None) -> pd.DataFrame:
    """gravite_urgences.csv : bloc classe_age × situation × tranche_h, repli classe_age × situation."""
    return table_proportions(effectifs, ["classe_age", "situation", "tranche_h"], ["gravite"],
                             replis=[["classe_age", "situation"]], seuil=seuil, contexte=contexte)


# ---------------------------------------------------------------------------
# Tension du jour (classes_tension.csv) et série quotidienne
# ---------------------------------------------------------------------------

def tension_journaliere(sejours: pd.DataFrame, debut, fin,
                        contexte: Sequence[str] | None = None) -> pd.DataFrame:
    """Série quotidienne de tension : par jour (et par site), patients présents
    au relevé, et parmi eux ceux venus des urgences ; la tension est ce second
    nombre, en valeur absolue (les lits réellement ouverts n'étant pas connus
    dans les données, on ne divise pas ; le simulateur, qui les connaît,
    ajuste les bornes au prorata des lits ouverts dans les scénarios).

    ``sejours`` : ``date_entree``, ``date_sortie``, ``urgences`` (1 si venu des
    urgences, UHCD comprise), plus le site. Présent le jour J : entré avant J
    et sorti le jour J ou après (dates PMSI sans heure, relevé du matin).
    ``debut``, ``fin`` : premier et dernier jour de la série.
    """
    jours = pd.date_range(debut, fin, freq="D")
    cles = avec_site([], sejours, contexte)
    entree = pd.to_datetime(sejours["date_entree"]).dt.normalize()
    sortie = pd.to_datetime(sejours["date_sortie"]).dt.normalize()
    urg = sejours["urgences"].fillna(0).astype(int).values
    n = len(jours) + 1                      # une case de plus, pour les sorties après la fin de la série
    i_deb = ((entree - jours[0]).dt.days + 1).clip(lower=0, upper=n - 1).values
    i_fin = ((sortie - jours[0]).dt.days + 1).clip(lower=0, upper=n - 1).values
    groupes = sejours.groupby(cles).indices if cles else {(): np.arange(len(sejours))}
    lignes = []
    for cle, idx in groupes.items():
        cle = cle if isinstance(cle, tuple) else (cle,)
        total, urgences = np.zeros(n), np.zeros(n)
        np.add.at(total, i_deb[idx], 1); np.add.at(total, i_fin[idx], -1)
        np.add.at(urgences, i_deb[idx], urg[idx]); np.add.at(urgences, i_fin[idx], -urg[idx])
        present, present_urg = np.cumsum(total)[:len(jours)], np.cumsum(urgences)[:len(jours)]
        t = pd.DataFrame({"date": jours, "presents": present.astype(int), "presents_urgences": present_urg.astype(int)})
        t["tension"] = t["presents_urgences"]
        for c, v in zip(cles, cle):
            t.insert(0, c, v)
        lignes.append(t)
    return pd.concat(lignes, ignore_index=True)


def classes_tension_depuis_tension(tension: pd.DataFrame, quantiles=(1 / 3, 2 / 3),
                                   contexte: Sequence[str] | None = None) -> pd.DataFrame:
    """classes_tension.csv : bornes de la tension (terciles par défaut), par site,
    en nombre de patients présents venus des urgences.
    Colonnes : classe (basse, moyenne, haute), borne_inf, borne_sup."""
    cles = avec_site([], tension, contexte)
    libelles = ["basse", "moyenne", "haute"]

    def bornes(serie: pd.Series) -> pd.DataFrame:
        q = [round(float(serie.quantile(x)), 1) for x in quantiles]
        inf = ["", *q]
        sup = [*q, ""]
        return pd.DataFrame({"classe": libelles, "borne_inf": inf, "borne_sup": sup})

    if not cles:
        return bornes(tension["tension"])
    return (tension.groupby(cles)["tension"].apply(bornes).reset_index(level=cles)
            .reset_index(drop=True))


def classe_tension(tension: pd.Series, bornes: pd.DataFrame) -> pd.Series:
    """Classe de tension d'une série de valeurs, d'après une table de bornes
    (d'un seul site : filtrer avant)."""
    seuils = [float(x) for x in bornes["borne_sup"] if x != ""]
    return pd.cut(tension, bins=[-np.inf, *seuils, np.inf], labels=bornes["classe"].tolist(), right=False).astype(str)


# ---------------------------------------------------------------------------
# Durées de passage aux urgences (durees_passage_urgences.csv)
# ---------------------------------------------------------------------------

def classer_tension(tension: pd.DataFrame, bornes: pd.DataFrame,
                    contexte: Sequence[str] | None = None) -> pd.DataFrame:
    """Ajoute à la série quotidienne la colonne ``classe_tension``, site par site,
    d'après la table de bornes (classes_tension)."""
    cles = avec_site([], tension, contexte)
    t = tension.copy()
    t["date"] = pd.to_datetime(t["date"]).dt.normalize()
    if not cles:
        t["classe_tension"] = classe_tension(t["tension"], bornes)
        return t
    morceaux = []
    for cle, part in t.groupby(cles):
        cle = cle if isinstance(cle, tuple) else (cle,)
        masque = np.logical_and.reduce([bornes[c] == v for c, v in zip(cles, cle)])
        part = part.copy()
        part["classe_tension"] = classe_tension(part["tension"], bornes[masque])
        morceaux.append(part)
    return pd.concat(morceaux, ignore_index=True)


def _venues_preparees(v: pd.DataFrame, tension_classee: pd.DataFrame,
                      premier_service_racine: pd.DataFrame | None,
                      contexte: Sequence[str] | None) -> tuple[pd.DataFrame, list[str]]:
    """Venues avec situation, gravité, durée en heures, tranche, classe de tension
    du jour d'arrivée et, si la table est fournie, rang du premier service."""
    cles = avec_site([], v, contexte)
    t = v.copy()
    t["situation"] = situation_venue(t)
    t = t[t["situation"].ne("inconnu")]
    avec_sejour = t["situation"].isin(REGLAGES.situations_avec_sejour)
    t["gravite"] = np.where(avec_sejour, "", gravite_ccmu(t["ccmu"]))
    ccmu_inconnue = ~avec_sejour & t["gravite"].eq("")      # gravité vide = ligne de repli : on ne peut pas garder ces venues ici
    if ccmu_inconnue.any():
        print(f"{ccmu_inconnue.sum()} venues non hospitalisées sans CCMU ({ccmu_inconnue.mean():.1%}), "
              "écartées du calcul des durées de passage")
        t = t[~ccmu_inconnue]
    t["duree_h"] = (pd.to_datetime(t["date_sortie"]) - pd.to_datetime(t["date_entree"])).dt.total_seconds() / 3600
    t = t[t["duree_h"].ge(0)].copy()
    t["tranche_h"] = tranche_heures(t["duree_h"])
    t["date"] = pd.to_datetime(t["date_entree"]).dt.normalize()
    t = t.merge(tension_classee[cles + ["date", "classe_tension"]], on=cles + ["date"], how="left")
    sans_classe = t["classe_tension"].isna()
    if sans_classe.any():
        print(f"{sans_classe.mean():.1%} de venues sans classe de tension (jour absent de la série), exclues")
        if cles:   # le détail par site : un site absent de la série d'occupation perd toutes ses venues
            print(sans_classe.groupby([t[c] for c in cles]).mean().rename("part_exclue").round(3).to_string())
        t = t[~sans_classe]
    if premier_service_racine is not None:
        if "premier_service" not in t.columns:
            raise KeyError("la colonne premier_service manque dans les venues ; ajoutez la spécialité du premier "
                           "RUM hors UHCD du séjour chaîné (jointure sur le numéro de séjour)")
        t["racine_ghm"] = t["ghm"].astype(str).str[:5]
        t["_rang_fin"] = pd.array([pd.NA] * len(t), dtype="Int64")
        t["_rang_repli"] = pd.array([pd.NA] * len(t), dtype="Int64")
        hosp = t["situation"].eq("hospitalisation")
        ps = premier_service_racine.copy()
        ps["classe_age"] = ps["classe_age"].astype(str)
        groupes = t.groupby(cles) if cles else [((), t)]
        for cle, part in groupes:
            cle = cle if isinstance(cle, tuple) else (cle,)
            if cles and all(c in ps.columns for c in cles):      # table déjà calculée par site : rien à restreindre
                table_site = ps[np.logical_and.reduce([ps[c] == v for c, v in zip(cles, cle)])]
            else:
                # table multi-sites : restreinte aux services de ce site, comme le fera le simulateur avec
                # structure.yaml (ici, les services observés comme premier service sur le site)
                services = part.loc[hosp[part.index], "premier_service"].dropna().unique()
                table_site = restreindre_services(ps, services, ["racine_ghm", "classe_age"])
            for niveau, colonne in (("fin", "_rang_fin"), ("repli", "_rang_repli")):
                r = table_site[table_site["classe_age"].ne("") if niveau == "fin" else table_site["classe_age"].eq("")]
                cles_niveau = ["racine_ghm", "premier_service"] + (["classe_age"] if niveau == "fin" else [])
                r = r.rename(columns={"specialite": "premier_service"})[cles_niveau + ["rang"]].drop_duplicates(cles_niveau)
                m = part[cles_niveau].merge(r, on=cles_niveau, how="left")
                t.loc[part.index, colonne] = m["rang"].to_numpy()
        # le niveau fin quand il existe (bloc fiable), sinon le repli par racine seule
        t["rang_premier"] = t["_rang_fin"].where(t["_rang_fin"].notna(), t["_rang_repli"]).astype("Int64")
        repli = hosp & t["_rang_fin"].isna()
        if hosp.any():
            print(f"{repli.sum()} venues hospitalisées ({repli.sum() / hosp.sum():.1%}) sans bloc racine × âge fiable : "
                  "rang pris sur le repli par racine ; rangs du premier service :",
                  {("sans rang" if pd.isna(k) else int(k)): int(n) for k, n in t.loc[hosp, "rang_premier"].value_counts(dropna=False).items()})
        t = t.drop(columns=["_rang_fin", "_rang_repli"])
    return t, cles


def effectifs_durees_urgences(v: pd.DataFrame, tension_classee: pd.DataFrame,
                              premier_service_racine: pd.DataFrame | None = None,
                              contexte: Sequence[str] | None = None) -> pd.DataFrame:
    """Effectifs des venues par situation × gravité × classe d'âge × classe de
    tension × tranche de durée.

    ``v`` : les venues (colonnes de ``effectifs_venues`` plus ``date_sortie`` et,
    pour le filtre de rang, ``premier_service``). ``tension_classee`` : la série
    quotidienne avec ``classe_tension`` (``classer_tension``), jointe sur le
    site et la date d'arrivée. ``premier_service_racine`` : si fournie, seules
    les venues ``hospitalisation`` dont le premier service est de rang 1 sont
    gardées (la redirection est décrite par ``supplement_rang2``). La gravité
    n'est remplie que pour les situations sans séjour.
    """
    t, cles = _venues_preparees(v, tension_classee, premier_service_racine, contexte)
    if premier_service_racine is not None:
        hosp = t["situation"].eq("hospitalisation")
        exclus = hosp & t["rang_premier"].ne(1).fillna(True)
        print(f"{exclus.sum()} venues hospitalisées hors rang 1 écartées de la table des durées "
              f"({exclus.sum() / max(hosp.sum(), 1):.1%} des hospitalisées)")
        t = t[~exclus]
    return t.groupby(cles + ["situation", "gravite", "classe_age", "classe_tension", "tranche_h"]).size().reset_index(name="n")


def durees_urgences_depuis_effectifs(effectifs: pd.DataFrame, seuil: int | None = None,
                                     contexte: Sequence[str] | None = None) -> pd.DataFrame:
    """durees_passage_urgences.csv : bloc situation × gravité × classe d'âge × classe de
    tension ; replis sans la tension, puis sans la gravité, puis la situation seule (une classe
    d'âge rare pour une situation, par exemple les transferts d'enfants d'un site sans urgences
    pédiatriques). Pour les situations avec séjour (gravité toujours vide), des replis coïncident :
    on dédoublonne."""
    t = table_proportions(effectifs, ["situation", "gravite", "classe_age", "classe_tension"], ["tranche_h"],
                          replis=[["situation", "gravite", "classe_age"], ["situation", "classe_age"], ["situation"]],
                          seuil=seuil, contexte=contexte, seuil_lissage=1)     # pas de lissage : catégories ordonnées (durées)
    cles = [c for c in t.columns if c != "part"]
    return t.drop_duplicates(subset=cles).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Supplément d'attente avant un service de rang 2 (supplement_rang2.csv)
# ---------------------------------------------------------------------------

def effectifs_supplement_rang2(v: pd.DataFrame, tension_classee: pd.DataFrame,
                               premier_service_racine: pd.DataFrame,
                               contexte: Sequence[str] | None = None) -> pd.DataFrame:
    """Effectifs du supplément de durée des hospitalisés de rang 2, par classe de
    tension × tranche. Supplément = durée de passage − durée médiane des
    hospitalisés de rang 1 de la même case (site, classe d'âge, classe de
    tension) ; les écarts négatifs sont ramenés à zéro."""
    t, cles = _venues_preparees(v, tension_classee, premier_service_racine, contexte)
    hosp = t[t["situation"].eq("hospitalisation")]
    case = cles + ["classe_age", "classe_tension"]
    mediane_r1 = (hosp[hosp["rang_premier"].eq(1)].groupby(case)["duree_h"].median()
                  .rename("mediane_rang1").reset_index())
    r2 = hosp[hosp["rang_premier"].eq(2)].merge(mediane_r1, on=case, how="left")
    sans_ref = r2["mediane_rang1"].isna()
    if sans_ref.any():
        print(f"{sans_ref.sum()} venues de rang 2 sans référence de rang 1 dans leur case, exclues")
        r2 = r2[~sans_ref]
    print(f"{len(r2)} venues de rang 2 ({len(r2) / max(len(hosp), 1):.1%} des hospitalisées)")
    supplement = (r2["duree_h"] - r2["mediane_rang1"]).clip(lower=0)
    r2 = r2.assign(tranche_h=tranche_heures(supplement))
    return r2.groupby(cles + ["classe_tension", "tranche_h"]).size().reset_index(name="n")


def supplement_depuis_effectifs(effectifs: pd.DataFrame, seuil: int | None = None,
                                contexte: Sequence[str] | None = None) -> pd.DataFrame:
    """supplement_rang2.csv : bloc classe de tension, repli sur l'ensemble (classe vide)."""
    return table_proportions(effectifs, ["classe_tension"], ["tranche_h"], replis=[[]],
                             seuil=seuil, contexte=contexte, seuil_lissage=1)      # pas de lissage : catégories ordonnées (durées)


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

def etapes_sejours(rum: pd.DataFrame, contexte: Sequence[str] | None = None) -> pd.DataFrame:
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
        avec_site(["id_sejour", "service", "precedent", "classe_age", "mode_entree", "suite"], df, contexte)]


def effectifs_passage(etapes: pd.DataFrame, contexte: Sequence[str] | None = None) -> pd.DataFrame:
    """Compte les transitions aux deux niveaux. Colonnes : contexte + ``n``."""
    morceaux = []
    for niveau, colonnes in REGLAGES.blocs_passage.items():
        n = etapes.groupby(avec_site(colonnes, etapes, contexte) + ["suite"]).size().reset_index(name="n")
        n["niveau"] = niveau
        morceaux.append(n)
    return (pd.concat(morceaux, ignore_index=True)
            .reindex(columns=avec_site(REGLAGES.colonnes_contexte_passage, etapes, contexte) + ["n"]).fillna(""))


def table_passage_depuis_effectifs(effectifs: pd.DataFrame, seuil: int | None = None,
                                   part_min: float | None = None, contexte: Sequence[str] | None = None) -> pd.DataFrame:
    """table_passage.csv : normalisation par niveau et rang des suites."""
    morceaux = []
    for niveau, colonnes in REGLAGES.blocs_passage.items():
        e = effectifs[effectifs["niveau"] == niveau]
        # colonnes_categorie=["suite"] : les effectifs sont additionnés sur les colonnes absentes du bloc
        # (le site, quand la table est calculée sur tous les sites avec contexte=[])
        t = normaliser(e, colonnes, colonnes_categorie=["suite"], seuil=seuil, contexte=contexte)
        t["niveau"] = niveau
        t["rang"] = attribuer_rang(t, part_min=part_min, masque=t["suite"].ne("sortie"))
        morceaux.append(t)
    return (pd.concat(morceaux, ignore_index=True)
            .reindex(columns=avec_site(REGLAGES.colonnes_contexte_passage, effectifs, contexte) + ["part", "rang"]))


# ---------------------------------------------------------------------------
# Durées de passage par spécialité (durees_passage.csv)
# ---------------------------------------------------------------------------

def passages_specialite(rum: pd.DataFrame, contexte: Sequence[str] | None = None) -> pd.DataFrame:
    """Une ligne par passage de spécialité (RUM consécutifs de même spécialité
    fusionnés), avec son séjour et sa durée en jours décimaux.

    Entrée : les RUM (pas la sortie de cette fonction). Colonnes attendues : ``id_sejour``, ``rang``, ``specialite`` (UHCD déjà
    retirée), ``date_entree``, ``date_sortie`` (datetime), ``mode_entree``,
    ``classe_age``.
    """
    df = rum.sort_values(["id_sejour", "rang"]).reset_index(drop=True)
    for c in ("date_entree", "date_sortie"):          # dates sans heure ou en texte selon le pilote : on force le type
        df[c] = pd.to_datetime(df[c])
    nouveau = ~(df["id_sejour"].eq(df["id_sejour"].shift()) & df["specialite"].eq(df["specialite"].shift()))
    df["id_passage"] = nouveau.cumsum()
    premieres = {c: (c, "first") for c in avec_site(["id_sejour", "specialite", "mode_entree", "classe_age"], df, contexte)}
    p = df.groupby("id_passage").agg(**premieres, date_entree=("date_entree", "min"), date_sortie=("date_sortie", "max"))
    p["duree_j"] = (p["date_sortie"] - p["date_entree"]).dt.total_seconds() / 86400
    # les dates sont gardées : elles servent aux présents quotidiens par service (lits ouverts, cibles)
    return p[avec_site(["id_sejour", "specialite", "mode_entree", "classe_age", "date_entree", "date_sortie", "duree_j"], df, contexte)].reset_index(drop=True)


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
# Cibles du tableau de contrôle (controle/cibles.csv)
# ---------------------------------------------------------------------------

def cible(indicateur: str, observe: float, tolerance: float = 0.10, absolue: bool = False,
          service: str = "", classe_tension: str = "", **contexte) -> dict:
    """Une ligne de cibles.csv : fourchette = observé ± tolérance, relative par
    défaut (0,10 = ± 10 %) ou absolue (``absolue=True`` : ± tolérance).
    ``contexte`` : colonnes de site (nohop="H1")."""
    marge = abs(observe) * tolerance if not absolue else tolerance
    return {**contexte, "indicateur": indicateur, "service": service, "classe_tension": classe_tension,
            "observe": round(float(observe), 4), "min": round(float(observe - marge), 4), "max": round(float(observe + marge), 4)}


# ---------------------------------------------------------------------------
# Contrôles
# ---------------------------------------------------------------------------

def verifier_table(t: pd.DataFrame, colonnes_bloc: Sequence[str], contexte: Sequence[str] | None = None) -> None:
    """Vérifie qu'une table de proportions somme à 1 dans chaque bloc (+ site)."""
    sommes = t.groupby(avec_site(colonnes_bloc, t, contexte))["part"].sum().round(4)
    fautives = sommes[sommes != 1]
    if len(fautives):
        raise ValueError(f"{len(fautives)} blocs ne somment pas à 1 :\n{fautives.head()}")


# ---------------------------------------------------------------------------
# Vérification d'un dossier de paramètres (les 22 fichiers)
# ---------------------------------------------------------------------------

# nom : (colonnes attendues, colonnes de bloc du niveau fin, colonnes vides sur les lignes de repli)
ATTENDUS = {
    "niveaux": (["racine_ghm", "classe_age", "niveau", "part"], ["racine_ghm", "classe_age"], ["classe_age"]),
    "durees_ghm": (["ghm", "classe_age_duree", "duree_j", "part"], ["ghm", "classe_age_duree"], ["classe_age_duree"]),
    "sexe_racine": (["racine_ghm", "sexe", "part"], ["racine_ghm"], []),
    "sorties": (["racine_ghm", "niveau", "classe_age", "mode_sortie", "part"], ["racine_ghm", "niveau", "classe_age"], ["niveau", "classe_age"]),
    "premier_service_racine": (["racine_ghm", "classe_age", "specialite", "part", "rang"], ["racine_ghm", "classe_age"], ["classe_age"]),
    "casemix": (["specialite", "classe_age", "racine_ghm", "part"], ["specialite"], []),
    "table_passage": (["niveau", "service", "precedent", "classe_age", "mode_entree", "suite", "part", "rang"],
                      ["niveau", "service", "precedent", "classe_age", "mode_entree"], ["precedent", "classe_age", "mode_entree"]),
    "venues_urgences": (["saison", "periode", "classe_age", "situation", "gravite", "racine_ghm", "part"], ["saison", "periode", "classe_age"], ["periode"]),
    "gravite_urgences": (["classe_age", "situation", "tranche_h", "gravite", "part"], ["classe_age", "situation", "tranche_h"], ["tranche_h"]),
    "durees_passage_urgences": (["situation", "gravite", "classe_age", "classe_tension", "tranche_h", "part"],
                                ["situation", "gravite", "classe_age", "classe_tension"], ["classe_tension", "gravite", "classe_age"]),
    "supplement_rang2": (["classe_tension", "tranche_h", "part"], ["classe_tension"], ["classe_tension"]),
    "profil_patients_urgences": (["classe_age", "sexe", "part"], [], []),
    "profil_arrivees_urgences": (["saison", "dimension", "jour_semaine", "heure", "mois", "part"], ["saison", "dimension"], []),
    "profil_entrees_programmees": (["dimension", "jour_semaine", "heure", "part"], ["dimension"], []),
    "profil_sorties": (["dimension", "jour_semaine", "heure", "part"], ["dimension"], []),
}
AUTRES = ["structure.yaml", "specialites.csv", "regroupements_ghm.csv", "soins_critiques_ghm.csv",
          "durees_passage.csv", "classes_tension.csv", "cibles.csv"]


def sommes_fautives(t, bloc, tolerance):
    """Blocs dont la somme des parts s'écarte de 1 de plus que la tolérance (Series somme, avec n lignes)."""
    g = t.groupby(bloc)["part"] if bloc else t.assign(_un=1).groupby("_un")["part"]
    bilan = g.agg(somme="sum", lignes="size")
    return bilan[(bilan["somme"] - 1).abs() > tolerance]


def verifier_dossier(dossier, contexte: Sequence[str] = (), tolerance: float = 1e-3) -> bool:
    """Vérifie un dossier de paramètres : pour chacun des 22 fichiers attendus, présence,
    colonnes, et sommes à 1 par bloc (niveau fin, puis niveaux de repli). Imprime une
    ligne par fichier et renvoie True si tout est bon. ``contexte`` : colonnes de site
    présentes dans les fichiers (normalement aucune : les fichiers décrivent un site)."""
    dossier = Path(dossier)
    tout_bon = True
    for nom, (colonnes, bloc, replis) in ATTENDUS.items():
        f = dossier / f"{nom}.csv"
        if not f.exists():
            print(f"MANQUANT  {nom}"); tout_bon = False; continue
        t = pd.read_csv(f, dtype=str, keep_default_na=False)
        manquantes = [c for c in colonnes if c not in t.columns]
        if manquantes:
            print(f"COLONNES  {nom} : manque {manquantes} ; colonnes du fichier : {list(t.columns)}"); tout_bon = False; continue
        t["part"] = t["part"].astype(float)
        # niveau fin : aucune colonne de repli vide ; replis : regroupés selon les colonnes vides
        vides = (t[replis] == "").any(axis=1) if replis else pd.Series(False, index=t.index)
        fautives = [sommes_fautives(t[~vides], bloc, tolerance)]
        if vides.any():
            for _, part in t[vides].groupby([t.loc[vides, c] == "" for c in replis]):
                fautives.append(sommes_fautives(part, [c for c in bloc if (part[c] != "").all()], tolerance))
        fautives = pd.concat(fautives)
        if len(fautives):
            print(f"SOMMES    {nom} : {len(fautives)} blocs ne somment pas à 1 (tolérance {tolerance}) ; premiers :")
            print(fautives.head().to_string()); tout_bon = False
        else:
            print(f"ok        {nom} ({len(t)} lignes)")
    for nom in AUTRES:
        present = (dossier / nom).exists()
        tout_bon &= present
        print(("ok        " if present else "MANQUANT  ") + nom)
    return tout_bon
