"""Fabrique notebooks/preparation_parametres.ipynb à partir de parametres/DICTIONNAIRE.md.

Le notebook reprend le dictionnaire en cellules markdown, avec le récapitulatif
en tête (liens vers chaque fichier) et, sous la description de chaque fichier,
une cellule de code vide pour y écrire l'extraction correspondante.

    python scripts/fabriquer_notebook_dictionnaire.py
"""

from __future__ import annotations

import re
from pathlib import Path

import nbformat
from nbformat.v4 import new_code_cell, new_markdown_cell, new_notebook

RACINE = Path(__file__).resolve().parents[1]
SOURCE = RACINE / "parametres" / "DICTIONNAIRE.md"
SORTIE = RACINE / "notebooks" / "preparation_parametres.ipynb"

CELLULE_REGLAGES = '''# Les fonctions mises au point dans le carnet de bord sont dans gen_admissions/preparation_parametres.py\n# (normaliser, attribuer_rang, table_proportions, additionner, effectifs_venues, effectifs_arrivees,\n#  profil_depuis_effectifs, etapes_sejours, passages_specialite, premier_service_depuis_effectifs…).
# Le plus simple est d'installer le dépôt une fois pour toutes (depuis sa racine) : pip install -e .
# À défaut, on retrouve la racine du dépôt en remontant depuis le dossier courant.
import sys
from pathlib import Path

try:
    import gen_admissions
except ModuleNotFoundError:
    racine = next(p for p in [Path.cwd(), *Path.cwd().parents] if (p / "gen_admissions").is_dir())
    sys.path.insert(0, str(racine))
from gen_admissions.preparation_parametres import *

import pandas as pd
import numpy as np

# Réglages de normalisation : définis ici, et passés explicitement à chaque appel, pour que chaque
# cellule dise avec quelles valeurs elle calcule. Par exemple :
#     sexe_racine = normaliser(df_s_rac, colonnes_bloc=["racine_ghm"], contexte=[], seuil=SEUIL)        # sans site
#     venues = venues_depuis_effectifs(effectifs, contexte=SITE, seuil=SEUIL)                            # par site
#     tp = table_passage_depuis_effectifs(effectifs, contexte=SITE, seuil=SEUIL, part_min=PART_MIN)
# `contexte` est la liste des colonnes placées en tête de tous les blocs de normalisation (le site) ;
# sans `contexte`, la colonne de site de configurer() est utilisée si elle est présente dans le tableau.
SEUIL = 100                          # séjours, sur la période d'observation
PART_MIN = 0.10                      # part à partir de laquelle un service est de rang 1
SITE = ["nohop"]                     # colonne(s) de site ; [] pour un calcul sans site

# Réglages de structure (classes, saisons, listes de colonnes), lus par toutes les fonctions :
configurer(
    seuil_effectif=SEUIL,                # valeur par défaut si un appel ne précise pas seuil=
    part_min_service_habituel=PART_MIN,  # idem pour part_min=
    colonnes_site=tuple(SITE),           # défaut des appels sans contexte=, quand la colonne est présente dans le tableau
    bornes_age=(17, 64, 74),             # 0-17, 18-64, 65-74, 75+
    bornes_age_duree=(59,),              # 0-59, 60+
    bornes_tranches_h=(1, 2, 4, 6, 12, 24),
    colonnes_saison=("saison",),         # saison épidémiologique (sept-août) dans les tables saisonnières ; v["saison"] = saison(v["date_entree"])
    mois_debut_saison=9,
)
print(REGLAGES)

DOSSIER_SORTIE = "parametres/mon_etablissement"   # où écrire les fichiers de proportions
DOSSIER_TRAVAIL = "travail"                        # effectifs intermédiaires par année (non diffusés)
ANNEES = range(2020, 2025)                         # tables de comptage par GHM, racine, parcours : plusieurs années
ANNEE_RECENTE = 2024                               # profils, durées, tension : la dernière année suffit
connexion = None                                   # à remplacer par votre connexion à la base
'''

CELLULE_BILAN_MD = """## Bilan : les 22 fichiers attendus

Quand toutes les cellules ont été exécutées, le dossier de sortie doit contenir
les 22 fichiers du dictionnaire :

| Groupe | Fichiers |
| --- | --- |
| Structure | `structure.yaml`, `specialites.csv`, `regroupements_ghm.csv` |
| Par GHM | `niveaux.csv`, `durees_ghm.csv`, `soins_critiques_ghm.csv`, `sexe_racine.csv`, `sorties.csv` |
| Pratiques | `premier_service_racine.csv`, `casemix.csv`, `table_passage.csv`, `durees_passage.csv` |
| Urgences | `venues_urgences.csv`, `profil_arrivees_urgences.csv`, `profil_patients_urgences.csv`, `gravite_urgences.csv`, `classes_tension.csv`, `durees_passage_urgences.csv`, `supplement_rang2.csv` |
| Programmé et sorties | `profil_entrees_programmees.csv`, `profil_sorties.csv` |
| Contrôle | `cibles.csv` |

La cellule suivante vérifie, pour chaque fichier, sa présence, ses colonnes et,
pour les tables de proportions, que les parts somment à 1 dans chaque bloc
(les lignes de repli, dont une colonne de bloc est vide, sont vérifiées à part,
niveau par niveau).

Ces fichiers ne contiennent que des proportions, mais ils décrivent un site
réel : à garder hors du dépôt public tant que leur diffusion n'est pas décidée.
"""

CELLULE_BILAN_CODE = '''# Vérification du dossier de sortie : présence, colonnes, sommes à 1 par bloc
from pathlib import Path

# nom : (colonnes attendues, colonnes de bloc du niveau fin, colonnes qui sont vides sur les lignes de repli)
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
                                ["situation", "gravite", "classe_age", "classe_tension"], ["classe_tension", "gravite"]),
    "supplement_rang2": (["classe_tension", "tranche_h", "part"], ["classe_tension"], ["classe_tension"]),
    "profil_patients_urgences": (["classe_age", "sexe", "part"], [], []),
    "profil_arrivees_urgences": (["saison", "dimension", "jour_semaine", "heure", "mois", "part"], ["saison", "dimension"], []),
    "profil_entrees_programmees": (["dimension", "jour_semaine", "heure", "part"], ["dimension"], []),
    "profil_sorties": (["dimension", "jour_semaine", "heure", "part"], ["dimension"], []),
}
AUTRES = ["structure.yaml", "specialites.csv", "regroupements_ghm.csv", "soins_critiques_ghm.csv",
          "durees_passage.csv", "classes_tension.csv", "cibles.csv"]


def verifier_dossier(dossier, contexte):
    dossier = Path(dossier)
    for nom, (colonnes, bloc, replis) in ATTENDUS.items():
        f = dossier / f"{nom}.csv"
        if not f.exists():
            print(f"MANQUANT  {nom}"); continue
        t = pd.read_csv(f, dtype=str, keep_default_na=False)
        t["part"] = t["part"].astype(float)
        manquantes = [c for c in colonnes if c not in t.columns]
        if manquantes:
            print(f"COLONNES  {nom} : manque {manquantes}"); continue
        # le niveau fin : aucune colonne de bloc vide ; chaque niveau de repli : vérifié avec ses colonnes non vides
        vides = (t[replis] == "").any(axis=1) if replis else pd.Series(False, index=t.index)
        try:
            verifier_table(t[~vides], bloc, contexte=contexte)
            for _, part in t[vides].groupby([t.loc[vides, c] == "" for c in replis]) if vides.any() else []:
                verifier_table(part, [c for c in bloc if (part[c] != "").all()], contexte=contexte)
            print(f"ok        {nom} ({len(t)} lignes)")
        except ValueError as e:
            print(f"SOMMES    {nom} : {str(e).splitlines()[0]}")
    for nom in AUTRES:
        print(("ok        " if (dossier / nom).exists() else "MANQUANT  ") + nom)


verifier_dossier(DOSSIER_SORTIE, SITE)
'''


def decouper(texte: str) -> tuple[list[str], list[tuple[int, str, list[str]]]]:
    """Sépare l'introduction des blocs (niveau de titre, titre, lignes du corps)."""
    blocs: list[tuple[int, str, list[str]]] = []
    courant: tuple[int, str, list[str]] = (0, "", [])
    for ligne in texte.split("\n"):
        m = re.match(r"^(#{2,3}) (.*)$", ligne)
        if m:
            blocs.append(courant)
            courant = (len(m.group(1)), m.group(2), [])
        else:
            courant[2].append(ligne)
    blocs.append(courant)
    return blocs[0][2], blocs[1:]


def ancre(titre: str) -> str:
    m = re.match(r"`([^`]+)`", titre)
    if m:
        return m.group(1).split("/")[-1].rsplit(".", 1)[0]
    return re.sub(r"[^a-z0-9]+", "-", titre.lower()).strip("-")


def corps(lignes: list[str]) -> str:
    t = "\n".join(lignes).strip("\n")
    t = re.sub(r"\n-{3,}\n", "\n", t)
    return t.strip()


def main() -> None:
    texte = SOURCE.read_text(encoding="utf-8")
    intro, blocs = decouper(texte)
    ancres = {ancre(titre) for niveau, titre, _ in blocs if niveau == 3}

    def lier(t: str) -> str:
        def rep(m: re.Match) -> str:
            nom = m.group(1).split("/")[-1].rsplit(".", 1)[0]
            return f"[`{m.group(1)}`](#{nom})" if nom in ancres else m.group(0)
        return re.sub(r"`([A-Za-z_/]+\.(?:csv|yaml))`", rep, t)

    nb = new_notebook()
    nb.metadata["kernelspec"] = {"name": "python3", "display_name": "Python 3", "language": "python"}
    nb.metadata["language_info"] = {"name": "python"}

    titre = "# Dictionnaire des paramètres du simulateur (version 2)"
    nb.cells.append(new_markdown_cell(corps(intro).replace(
        titre, titre + "\n\n*Version carnet de travail : sous la description de chaque fichier, "
        "une cellule de code est prête pour écrire le calcul correspondant.*")))

    recap = next(b for b in blocs if b[1].startswith("7."))
    nb.cells.append(new_markdown_cell(
        '<a id="recap"></a>\n\n## Récapitulatif : quel fichier pour quelle étape\n\n'
        "Chaque nom de fichier renvoie à sa description plus bas.\n\n" + lier(corps(recap[2]))))
    nb.cells.append(new_code_cell(CELLULE_REGLAGES))

    for niveau, titre_bloc, lignes in blocs:
        if titre_bloc.startswith("7."):
            continue
        if niveau == 2:
            nb.cells.append(new_markdown_cell(f"## {titre_bloc}\n\n{corps(lignes)}".rstrip()))
        else:
            nb.cells.append(new_markdown_cell(
                f'<a id="{ancre(titre_bloc)}"></a>\n\n### {titre_bloc}\n\n{corps(lignes)}\n\n[↑ récapitulatif](#recap)'))
            nb.cells.append(new_code_cell(f"# {titre_bloc}\n"))

    nb.cells.append(new_markdown_cell(CELLULE_BILAN_MD))
    nb.cells.append(new_code_cell(CELLULE_BILAN_CODE))
    nbformat.write(nb, SORTIE)
    print(len(nb.cells), "cellules ->", SORTIE)


if __name__ == "__main__":
    main()
