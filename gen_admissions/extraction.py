"""Extraction des paramètres agrégés à partir de données de type RUM.

Ce module ne contient que des calculs d'agrégats (effectifs, pourcentages,
médianes, quartiles). Il sert deux fois :

1. dans ``scripts/fabriquer_parametres.py``, sur des séjours fictifs, pour
   produire les paramètres de l'hôpital type ;
2. plus tard, sur les vraies données agrégées de l'établissement, exactement
   de la même façon (le format d'entrée est décrit ci-dessous).

Format d'entrée : un tableau pandas ``etapes`` avec une ligne par passage
dans une unité (l'équivalent d'un RUM, regroupé au niveau de la spécialité) :

    id_sejour          identifiant du séjour
    rang               ordre du passage dans le séjour (1, 2, 3…)
    specialite         spécialité de l'unité où le patient est physiquement
    specialite_prevue  spécialité dont relève le patient (= specialite sauf hébergement)
    heberge            True si le passage est un hébergement
    duree_j            durée du passage en jours (décimale)
    mode_entree        « urgences » ou « programme » (pour le séjour entier)
    classe_age         tranche d'âge du patient
    sexe               « M » ou « F »
    mode_sortie        mode de sortie final du séjour (domicile, SSR, EHPAD, transfert, deces)

Un seuil d'effectif minimal est appliqué partout : les cases qui contiennent
moins de ``seuil`` séjours ne sont pas exportées.
"""

from __future__ import annotations

import pandas as pd

AUCUN_PRECEDENT = "AUCUN"
SEPARATEUR = " > "


def fusionner_etapes(etapes: pd.DataFrame) -> pd.DataFrame:
    """Neutralise les hébergements et fusionne les passages consécutifs dans une même spécialité.

    Retourne un tableau avec une ligne par étape « médicale » du séjour :
    id_sejour, rang, specialite, duree_j (+ les variables de séjour).
    """
    df = etapes.sort_values(["id_sejour", "rang"]).copy()
    # Un hébergement compte pour la spécialité dont relève le patient.
    df["spec"] = df["specialite_prevue"].where(df["heberge"].astype(bool), df["specialite"])

    lignes = []
    for id_sejour, grp in df.groupby("id_sejour", sort=False):
        premiere = grp.iloc[0]
        courante, duree, rang = None, 0.0, 0
        for spec, d in zip(grp["spec"], grp["duree_j"]):
            if spec == courante:
                duree += float(d)
            else:
                if courante is not None:
                    lignes.append((id_sejour, rang, courante, duree))
                courante, duree, rang = spec, float(d), rang + 1
        lignes.append((id_sejour, rang, courante, duree))

    fusion = pd.DataFrame(lignes, columns=["id_sejour", "rang", "specialite", "duree_j"])
    variables = df.groupby("id_sejour", sort=False)[["mode_entree", "classe_age", "sexe", "mode_sortie"]].first()
    return fusion.merge(variables, left_on="id_sejour", right_index=True)


def trajectoires(etapes_fusionnees: pd.DataFrame) -> pd.DataFrame:
    """Une ligne par séjour : la suite des spécialités sous forme de texte « A > B > C »."""
    df = etapes_fusionnees.sort_values(["id_sejour", "rang"])
    suites = df.groupby("id_sejour", sort=False)["specialite"].agg(list)
    variables = df.groupby("id_sejour", sort=False)[["mode_entree", "classe_age", "sexe", "mode_sortie"]].first()
    out = variables.copy()
    out["etapes"] = suites
    out["trajectoire"] = suites.map(SEPARATEUR.join)
    out["premier_service"] = suites.map(lambda s: s[0])
    out["nb_etapes"] = suites.map(len)
    return out.reset_index()


def catalogue(traj: pd.DataFrame, seuil: int) -> pd.DataFrame:
    """Catalogue des trajectoires : effectif par (trajectoire, mode d'entrée, âge, sexe, sortie)."""
    cles = ["trajectoire", "mode_entree", "classe_age", "sexe", "mode_sortie"]
    cat = traj.groupby(cles).size().rename("effectif").reset_index()
    cat = cat[cat["effectif"] >= seuil]
    return cat.sort_values(["mode_entree", "classe_age", "sexe", "effectif"], ascending=[True, True, True, False]).reset_index(drop=True)


def hors_catalogue(traj: pd.DataFrame, cat: pd.DataFrame) -> pd.DataFrame:
    """Les séjours dont la trajectoire (avec son profil) n'est pas dans le catalogue.

    Le simulateur tire les séjours « fréquents » dans le catalogue et construit
    les autres avec la table de passage. Pour que l'ensemble reproduise bien
    les données, la table de passage doit donc être calculée sur ces autres
    séjours seulement, et non sur la totalité : sinon les parcours rares
    (passages en réanimation, par exemple) seraient sous-représentés.
    """
    cles = ["trajectoire", "mode_entree", "classe_age", "sexe", "mode_sortie"]
    couverts = cat[cles].assign(_couvert=True)
    fusion = traj.merge(couverts, on=cles, how="left")
    return fusion[fusion["_couvert"].isna()].drop(columns="_couvert")


def premier_service(traj: pd.DataFrame, seuil: int) -> pd.DataFrame:
    """Répartition du premier service du séjour selon le mode d'entrée, l'âge et le sexe.

    La colonne ``effectif`` sert aussi à calculer la part des séjours couverts
    par le catalogue (voir ``parcours.py``).
    """
    cles = ["mode_entree", "classe_age", "sexe"]
    comptes = traj.groupby(cles + ["premier_service"]).size().rename("effectif").reset_index()
    totaux = comptes.groupby(cles)["effectif"].transform("sum")
    comptes["probabilite"] = comptes["effectif"] / totaux
    comptes["effectif_case"] = totaux
    comptes = comptes[comptes["effectif_case"] >= seuil]
    return comptes.rename(columns={"premier_service": "service"}).reset_index(drop=True)


NIVEAUX_PASSAGE = {
    "complet": ["service", "precedent", "classe_age", "mode_entree"],
    "sans_precedent": ["service", "classe_age", "mode_entree"],
    "sans_age": ["service", "mode_entree"],
    "service": ["service"],
}


def table_passage(traj: pd.DataFrame, seuil: int) -> pd.DataFrame:
    """Table de passage : à la fin d'un passage dans un service, où va le patient ?

    Produit tous les niveaux de détail (du plus fin au plus grossier) dans un
    seul tableau, avec une colonne ``niveau``. Le simulateur choisit à
    l'exécution le niveau le plus fin qui contient assez de séjours.
    """
    transitions = []
    for etapes, classe_age, mode_entree, mode_sortie in zip(
        traj["etapes"], traj["classe_age"], traj["mode_entree"], traj["mode_sortie"]
    ):
        precedent = AUCUN_PRECEDENT
        for i, service in enumerate(etapes):
            suite = etapes[i + 1] if i + 1 < len(etapes) else mode_sortie
            transitions.append((service, precedent, classe_age, mode_entree, suite))
            precedent = service
    tr = pd.DataFrame(transitions, columns=["service", "precedent", "classe_age", "mode_entree", "suite"])

    morceaux = []
    for niveau, cles in NIVEAUX_PASSAGE.items():
        comptes = tr.groupby(cles + ["suite"]).size().rename("effectif").reset_index()
        comptes["effectif_case"] = comptes.groupby(cles)["effectif"].transform("sum")
        comptes["probabilite"] = comptes["effectif"] / comptes["effectif_case"]
        comptes = comptes[comptes["effectif_case"] >= seuil].copy()
        comptes["niveau"] = niveau
        for c in ["precedent", "classe_age", "mode_entree"]:
            if c not in comptes.columns:
                comptes[c] = ""
        morceaux.append(comptes[["niveau", "service", "precedent", "classe_age", "mode_entree", "suite", "probabilite", "effectif_case"]])
    return pd.concat(morceaux, ignore_index=True)


NIVEAUX_DUREE = {
    "complet": ["service", "mode_entree", "classe_age"],
    "sans_age": ["service", "mode_entree"],
    "service": ["service"],
}


def durees(etapes_fusionnees: pd.DataFrame, seuil: int) -> pd.DataFrame:
    """Durées de passage par service (médiane, quartiles, part des passages < 1 jour).

    La médiane et les quartiles sont calculés sur les passages d'au moins un
    jour ; les passages plus courts sont décrits par leur proportion.
    """
    df = etapes_fusionnees.rename(columns={"specialite": "service"})
    morceaux = []
    for niveau, cles in NIVEAUX_DUREE.items():
        lignes = []
        for cle, grp in df.groupby(cles):
            if len(grp) < seuil:
                continue
            cle = cle if isinstance(cle, tuple) else (cle,)
            courts = grp["duree_j"] < 1.0
            longs = grp.loc[~courts, "duree_j"]
            if len(longs) >= 3:
                q1, med, q3 = longs.quantile([0.25, 0.5, 0.75])
            else:
                q1 = med = q3 = 1.0
            lignes.append(dict(zip(cles, cle)) | {
                "niveau": niveau,
                "mediane_j": round(float(med), 2),
                "q1_j": round(float(q1), 2),
                "q3_j": round(float(q3), 2),
                "part_moins_1j": round(float(courts.mean()), 3),
                "effectif": int(len(grp)),
            })
        part = pd.DataFrame(lignes)
        for c in ["mode_entree", "classe_age"]:
            if c not in part.columns:
                part[c] = ""
        morceaux.append(part[["niveau", "service", "mode_entree", "classe_age", "mediane_j", "q1_j", "q3_j", "part_moins_1j", "effectif"]])
    return pd.concat(morceaux, ignore_index=True)
