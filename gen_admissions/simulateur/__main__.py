"""Ligne de commande du simulateur.

    python -m gen_admissions [dossier_parametres] [--sortie DOSSIER] [--graine N]
                             [--duree J] [--chauffe J] [--marge J] [--facteur-lits F] [--sans-csv]

(``python -m gen_admissions.simulateur`` et la commande ``gen-admissions`` sont équivalents.)

Sans dossier de paramètres, celui de ``config_locale.yaml`` (sinon l'hôpital fictif).
Écrit les tables de sortie, ``tension.parquet``, ``resume.yaml`` et ``controle.csv``,
puis imprime le résumé de la simulation et le bilan du contrôle.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pandas as pd

from gen_admissions.extraction import config_locale

from .controle import bilan, tableau_controle
from .export import ecrire
from .moteur import Simulation
from .parametres import Parametres


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m gen_admissions", description="Simulateur de flux hospitaliers")
    ap.add_argument("dossier_parametres", nargs="?", help="les 22 fichiers de paramètres (défaut : config_locale.yaml, sinon l'hôpital fictif)")
    ap.add_argument("--sortie", help="dossier des sorties (défaut : sorties/<etablissement>_<graine>)")
    ap.add_argument("--graine", type=int)
    ap.add_argument("--duree", type=int, help="jours simulés (période utile)")
    ap.add_argument("--chauffe", type=int)
    ap.add_argument("--marge", type=int)
    ap.add_argument("--facteur-lits", type=float, default=1.0, help="bornes de tension ajustées à un hôpital dont les lits sont multipliés par F")
    ap.add_argument("--sans-csv", action="store_true")
    args = ap.parse_args(argv)

    conf = config_locale()
    dossier = Path(args.dossier_parametres) if args.dossier_parametres else conf["dossier_parametres"]
    p = Parametres.charger(dossier)
    print(p.resume(), "\n")
    sim = Simulation(p, graine=args.graine, duree_jours=args.duree, chauffe_jours=args.chauffe, marge_fin_jours=args.marge, facteur_lits=args.facteur_lits)
    sortie = Path(args.sortie) if args.sortie else conf["racine"] / "sorties" / f"{p.structure.etablissement}_{sim.graine}"
    t0 = time.time()
    sim.lancer()
    print(f"simulation de {sim.horizon_h / 24:.0f} jours en {time.time() - t0:.0f} s\n")
    print(sim.resume(), "\n")
    tabl = ecrire(sim, sortie, csv=not args.sans_csv)
    tableau = tableau_controle(tabl, p, sim.duree_jours)
    tableau.to_csv(sortie / "controle.csv", index=False)
    with pd.option_context("display.width", 160, "display.max_rows", 200):
        print(tableau.round(3).to_string(index=False))
    print("\n" + bilan(tableau))
    print(f"\nsorties écrites dans {sortie}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
