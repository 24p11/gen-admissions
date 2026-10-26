"""Ligne de commande.

    python -m gen_admissions simuler parametres/hopital_type sorties/essai --graine 42 --duree-jours 365
    python -m gen_admissions controler parametres/hopital_type sorties/essai   (recalcule le contrôle)
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from . import config, controle, export, moteur


def commande_simuler(args: argparse.Namespace) -> int:
    p = config.charger(args.parametres)
    t0 = time.time()
    print(f"Simulation de {args.duree_jours or p.simulation['duree_jours']} jours (chauffe {args.chauffe_jours if args.chauffe_jours is not None else p.simulation.get('chauffe_jours', 0)} j)…")
    r = moteur.simuler(p, graine=args.graine, duree_jours=args.duree_jours, chauffe_jours=args.chauffe_jours, marge_fin_jours=args.marge_fin_jours)
    print(f"  terminée en {time.time() - t0:.1f} s : {len(r.passages)} passages, {len(r.sejours)} séjours simulés")
    t = export.ecrire(r, args.sortie, parquet=not args.sans_parquet)
    print("  tables exportées : " + ", ".join(f"{k} ({len(v)})" for k, v in t.items()))
    ind = controle.rapport(r, t, args.sortie)
    hors = ind[ind.get("statut", "") == "HORS CIBLE"] if "statut" in ind else ind.iloc[0:0]
    print(f"  contrôle : {len(ind)} indicateurs, {len(hors)} hors cible → {Path(args.sortie) / 'controle.md'}")
    for _, x in hors.iterrows():
        print(f"    HORS CIBLE  {x['indicateur']:32s} {x['service']:9s} {x['valeur']}  (cible {x['valeur_min']} – {x['valeur_max']})")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gen_admissions", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="commande", required=True)
    s = sub.add_parser("simuler", help="lance une simulation et exporte les tables")
    s.add_argument("parametres", help="dossier des fichiers de paramètres")
    s.add_argument("sortie", help="dossier de sortie")
    s.add_argument("--graine", type=int, default=None)
    s.add_argument("--duree-jours", type=int, default=None)
    s.add_argument("--chauffe-jours", type=int, default=None)
    s.add_argument("--marge-fin-jours", type=int, default=None)
    s.add_argument("--sans-parquet", action="store_true")
    s.set_defaults(fonction=commande_simuler)
    args = ap.parse_args(argv)
    return args.fonction(args)


if __name__ == "__main__":
    sys.exit(main())
