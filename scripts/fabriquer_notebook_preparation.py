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

CELLULE_REGLAGES = '''# Les fonctions mises au point dans le carnet de bord sont dans gen_admissions/preparation_parametres.py.
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

# Réglages : les valeurs ci-dessous remplacent celles du module pour toute la session.
configurer(
    seuil_effectif=100,                 # séjours, sur la période d'observation
    part_min_service_habituel=0.10,     # part à partir de laquelle un service est de rang 1
    bornes_age=(17, 64, 74),            # 0-17, 18-64, 65-74, 75+
    bornes_age_duree=(59,),             # 0-59, 60+
    bornes_tranches_h=(1, 2, 4, 6, 12, 24),
)
print(REGLAGES)

DOSSIER_SORTIE = "parametres/mon_etablissement"   # où écrire les fichiers de proportions
DOSSIER_TRAVAIL = "travail"                        # effectifs intermédiaires par année (non diffusés)
ANNEES = range(2020, 2025)
connexion = None                                   # à remplacer par votre connexion à la base
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

    nbformat.write(nb, SORTIE)
    print(len(nb.cells), "cellules ->", SORTIE)


if __name__ == "__main__":
    main()
