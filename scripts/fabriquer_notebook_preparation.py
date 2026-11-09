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

CELLULE_REGLAGES = '''# Les fonctions mises au point dans le carnet de bord sont dans gen_admissions/extraction.py\n# (normaliser, attribuer_rang, table_proportions, additionner, effectifs_venues, effectifs_arrivees,\n#  profil_depuis_effectifs, etapes_sejours, passages_specialite, premier_service_depuis_effectifs…).
# Le plus simple est d'installer le dépôt une fois pour toutes (depuis sa racine) : pip install -e .
# À défaut, on retrouve la racine du dépôt en remontant depuis le dossier courant.
import sys
from pathlib import Path

try:
    import gen_admissions
except ModuleNotFoundError:
    racine = next(p for p in [Path.cwd(), *Path.cwd().parents] if (p / "gen_admissions").is_dir())
    sys.path.insert(0, str(racine))
from gen_admissions.extraction import *

import pandas as pd
import numpy as np

# Réglages de normalisation : définis ici, et passés explicitement à chaque appel, pour que chaque
# cellule dise avec quelles valeurs elle calcule. Par exemple :
#     sexe_racine = normaliser(df_s_rac, colonnes_bloc=["racine_ghm"], contexte=[], seuil=SEUIL)        # sans site
#     venues = venues_depuis_effectifs(effectifs, contexte=SITE, seuil=SEUIL)                            # par site
#     tp = table_passage_depuis_effectifs(effectifs, contexte=SITE, seuil=SEUIL, part_min=PART_MIN)
# `contexte` est la liste des colonnes placées en tête de tous les blocs de normalisation (le site) ;
# sans `contexte`, la colonne de site de configurer() est utilisée si elle est présente dans le tableau.
SEUIL = 25                           # fiabilité d'un bloc : en dessous, le bloc n'est pas écrit et le repli sert
SEUIL_LISSAGE = 20                   # lissage des parts : les lignes de moins de n séjours mettent leur masse en commun
PART_MIN = 0.10                      # part à partir de laquelle un service est de rang 1 (rang 2 sinon)
SITE = ["nohop"]                     # colonne(s) de site ; [] pour un calcul sans site
# Les tables de pratiques (premier_service_racine, table_passage) se calculent sur tous les sites (contexte=[]) :
# le simulateur les restreint aux services de l'hôpital et recalcule les rangs au chargement.

# Réglages de structure (classes, saisons, listes de colonnes), lus par toutes les fonctions :
configurer(
    seuil_effectif=SEUIL,                # valeur par défaut si un appel ne précise pas seuil=
    seuil_lissage=SEUIL_LISSAGE,
    part_min_service_habituel=PART_MIN,  # idem pour part_min=
    colonnes_site=tuple(SITE),           # défaut des appels sans contexte=, quand la colonne est présente dans le tableau
    bornes_age=(17, 64, 74),             # 0-17, 18-64, 65-74, 75+
    bornes_age_duree=(59,),              # 0-59, 60+
    bornes_tranches_h=(1, 2, 4, 6, 12, 24),
    colonnes_saison=("saison",),         # saison épidémiologique (sept-août) dans les tables saisonnières ; v["saison"] = saison(v["date_entree"])
    mois_debut_saison=9,
)
print(REGLAGES)

# Les chemins propres à la machine viennent de config_locale.yaml à la racine du dépôt (non suivi par
# git ; modèle : config_locale.exemple.yaml). Sans ce fichier : l'hôpital fictif du dépôt et un dossier data/.
CONF = config_locale()          # config_locale(None) : ignorer config_locale.yaml et travailler sur l'hôpital fictif du dépôt
DOSSIER_SORTIE = CONF["dossier_parametres"]        # où écrire les 22 fichiers
DOSSIER_DATA = CONF["dossier_data"]                # résultats de requêtes mémorisés en parquet (lire_sql / memoriser)
NOHOP = CONF["site"]                               # le site exporté (None : pas de filtre de site à l'export)
ANNEES = range(2020, 2025)                         # tables de comptage par GHM, racine, parcours : plusieurs années
ANNEE_RECENTE = 2024                               # profils, durées, tension : la dernière année suffit
connexion = None                                   # à remplacer par votre connexion à la base
print(CONF)
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

CELLULE_BILAN_CODE = '''# Vérification du dossier de sortie : présence, colonnes, sommes à 1 par bloc (niveau fin puis replis).
# La liste des fichiers et de leurs colonnes attendues est ATTENDUS / AUTRES dans le module (FONCTIONS.md, section 13).
# Les fichiers exportés décrivent un seul site et n'ont plus de colonne de site : contexte [] (et non SITE).
verifier_dossier(DOSSIER_SORTIE, [])
'''


CELLULE_ENTREES_PAR_JOUR = '''# Entrées par jour sur l'année de référence — le même graphique que la cartographie trace sur les sorties
# simulées (section 6), avec la même fonction, pour comparer réel et simulé à l'œil : niveau, saison, semaine.
# Suppose `v` (les venues de l'année de référence, avec `date_entree`) et `rum` (les RUM hors UHCD de la même
# année, avec `id_sejour`, `rang`, `date_entree`, `mode_entree`). Les séjours d'une année PMSI sont ceux qui
# se terminent dans l'année : les derniers jours de décembre sont donc sous-comptés (séjours non encore clos).
from gen_admissions.graphiques import entrees_par_jour, tracer_entrees_par_jour

v_site = v[v["nohop"] == NOHOP]
premiers_site = rum[rum["nohop"] == NOHOP].sort_values(["id_sejour", "rang"]).drop_duplicates("id_sejour")
entrees_reel = entrees_par_jour(v_site["date_entree"], premiers_site["date_entree"], premiers_site["mode_entree"].eq("urgences"),
                                debut=f"20{AN_REF}-01-01", fin=f"20{AN_REF + 1}-01-01")
semaine_reel = tracer_entrees_par_jour(entrees_reel, f"Entrées par jour, année 20{AN_REF}, site {NOHOP} (observé)")
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
            if ancre(titre_bloc) == "venues_urgences":
                nb.cells.append(new_code_cell(CELLULE_ENTREES_PAR_JOUR))

    nb.cells.append(new_markdown_cell(CELLULE_BILAN_MD))
    nb.cells.append(new_code_cell(CELLULE_BILAN_CODE))
    nbformat.write(nb, SORTIE)
    print(len(nb.cells), "cellules ->", SORTIE)


if __name__ == "__main__":
    main()
