"""Construction des tables de sortie à partir du résultat de la simulation.

Tables produites (formats proches des formats réels, voir le plan, section 6) :

- ``rpu``          : un passage aux urgences par ligne ;
- ``rss``          : un séjour par ligne ;
- ``rum``          : un passage dans une unité par ligne, avec les codes PMSI ;
- ``mouvements``   : un changement de lit ou d'unité par ligne, à l'heure près ;
- ``verite_sejours`` et ``verite_passages`` : ce qui a réellement été simulé ;
- ``patients``     : la population fictive.

Seuls les passages et séjours commencés pendant la période de collecte
(après la période de chauffe) sont exportés.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from . import __version__
from .moteur import Resultat

# Codes inspirés du PMSI : mode d'entrée / de sortie et destination.
CODES_SORTIE = {
    "mutation": ("6", ""),
    "domicile": ("8", ""),
    "EHPAD": ("8", "7"),       # domicile, destination : structure d'hébergement médico-sociale
    "SSR": ("7", "2"),         # transfert, destination : soins médicaux et de réadaptation
    "transfert": ("7", "1"),   # transfert, destination : autre établissement MCO
    "deces": ("9", ""),
}
CODES_ENTREE = {"domicile": "8", "urgences": "8", "mutation": "6"}


def _fmt_dh(d: datetime | None) -> str:
    return "" if d is None else d.strftime("%Y-%m-%d %H:%M")


def _fmt_j(d: datetime | None) -> str:
    return "" if d is None else d.strftime("%Y-%m-%d")


def tables(r: Resultat) -> dict[str, pd.DataFrame]:
    """Transforme le résultat de la simulation en tables pandas."""
    debut, fin = r.chauffe_h, r.fin_collecte_h
    passages = [x for x in r.passages if debut <= x.arrivee < fin]
    ids_passages = {x.id for x in passages}
    # Un séjour venu des urgences suit son passage ; un séjour programmé suit sa date d'entrée.
    sejours = [s for s in r.sejours if s.entree is not None and (
        (s.id_passage in ids_passages) if s.id_passage else (debut <= s.entree < fin))]

    rpu = pd.DataFrame([{
        "id_passage": x.id, "id_patient": x.patient.id,
        "date_entree": _fmt_dh(r.date(x.arrivee)), "date_sortie": _fmt_dh(r.date(x.sortie)),
        "age": x.patient.age, "sexe": x.patient.sexe, "gravite_ccmu": x.gravite,
        "mode_sortie": x.mode_sortie or "", "destination": x.destination or "",
        "duree_passage_h": round(x.sortie - x.arrivee, 2) if x.sortie is not None else None,
        "id_sejour": x.id_sejour or "",
    } for x in passages])

    lignes_rss, lignes_rum, lignes_mvt, lignes_verite = [], [], [], []
    for s in sejours:
        code_sortie, dest_sortie = CODES_SORTIE.get(s.mode_sortie, ("", "")) if s.mode_sortie else ("", "")
        lignes_rss.append({
            "id_sejour": s.id, "id_patient": s.patient.id,
            "date_entree": _fmt_j(r.date(s.entree)), "date_sortie": _fmt_j(r.date(s.sortie)),
            "age": s.patient.age, "sexe": s.patient.sexe,
            "mode_entree": "8", "provenance": "5" if s.mode_entree == "urgences" else "",
            "mode_sortie": code_sortie, "destination": dest_sortie,
            "mode_sortie_libelle": s.mode_sortie or "", "nb_rum": len(s.rums),
            "id_passage": s.id_passage or "", "duree_sejour_j": round((s.sortie - s.entree) / 24.0, 2) if s.sortie is not None else None,
        })
        for rum in s.rums:
            cs, ds = CODES_SORTIE.get(rum.mode_sortie, ("", "")) if rum.mode_sortie else ("", "")
            lignes_rum.append({
                "id_sejour": s.id, "id_patient": s.patient.id, "rang": rum.rang, "unite": rum.unite,
                "date_entree": _fmt_j(r.date(rum.entree)), "date_sortie": _fmt_j(r.date(rum.sortie)),
                "mode_entree": CODES_ENTREE[rum.mode_entree], "provenance": "5" if rum.mode_entree == "urgences" else "",
                "mode_sortie": cs, "destination": ds, "heberge": "oui" if rum.heberge else "non",
                "specialite_prevue": rum.specialite_prevue, "lit": rum.lit,
                "duree_h": round(rum.sortie - rum.entree, 2) if rum.sortie is not None else None,
            })
        for m in s.mouvements:
            lignes_mvt.append({
                "id_sejour": s.id, "id_patient": s.patient.id, "date_heure": _fmt_dh(r.date(m.instant)), "type": m.type,
                "unite_depart": m.unite_depart or "", "lit_depart": m.lit_depart or "",
                "unite_arrivee": m.unite_arrivee or "", "lit_arrivee": m.lit_arrivee or "",
            })
        lignes_verite.append({
            "id_sejour": s.id, "mode_entree": s.mode_entree, "origine_parcours": s.origine_parcours,
            "trajectoire_prevue": s.trajectoire_prevue, "trajectoire_realisee": s.trajectoire_realisee,
            "nb_rum": len(s.rums), "nb_hebergements": s.nb_hebergements,
            "nb_retours_origine": sum(1 for x in s.rums if x.retour_origine),
            "attente_entree_h": round(s.attente_entree_h, 2), "attente_mutations_h": round(s.attente_mutations_h, 2),
            "termine": s.sortie is not None,
        })

    verite_passages = pd.DataFrame([{
        "id_passage": x.id, "issue": x.mode_sortie or "", "attente_lit_h": None if x.attente_lit_h is None else round(x.attente_lit_h, 2),
        "transfert_faute_de_lit": x.transfert_faute_de_lit,
    } for x in passages])

    patients = pd.DataFrame([{"id_patient": p.id, "age": p.age, "sexe": p.sexe, "classe_age": p.classe_age, "nb_venues": p.nb_venues}
                             for p in r.patients])
    colonnes_mvt = ["id_sejour", "id_patient", "date_heure", "type", "unite_depart", "lit_depart", "unite_arrivee", "lit_arrivee"]
    mouvements = pd.DataFrame(lignes_mvt, columns=colonnes_mvt).sort_values(["date_heure", "id_sejour"]).reset_index(drop=True)
    return {
        "rpu": rpu, "rss": pd.DataFrame(lignes_rss), "rum": pd.DataFrame(lignes_rum), "mouvements": mouvements,
        "verite_sejours": pd.DataFrame(lignes_verite), "verite_passages": verite_passages, "patients": patients,
    }


def ecrire(r: Resultat, dossier: str | Path, parquet: bool = True) -> dict[str, pd.DataFrame]:
    """Écrit les tables en CSV (et Parquet) avec un fichier de description."""
    dossier = Path(dossier)
    dossier.mkdir(parents=True, exist_ok=True)
    t = tables(r)
    for nom, df in t.items():
        df.to_csv(dossier / f"{nom}.csv", index=False)
        if parquet:
            df.to_parquet(dossier / f"{nom}.parquet", index=False)
    description = {
        "avertissement": "DONNÉES FICTIVES générées par simulation : aucun patient réel.",
        "generateur": f"gen-admissions {__version__}",
        "etablissement": r.parametres.structure.get("etablissement"),
        "parametres": str(r.parametres.dossier),
        "graine": r.graine,
        "debut_collecte": _fmt_dh(r.date(r.chauffe_h)),
        "fin_collecte": _fmt_dh(r.date(r.fin_collecte_h)),
        "chauffe_jours": r.chauffe_h / 24.0,
        "date_generation": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "tables": {nom: len(df) for nom, df in t.items()},
    }
    with open(dossier / "description.json", "w", encoding="utf-8") as f:
        json.dump(description, f, ensure_ascii=False, indent=2)
    return t
