"""Écrire les sorties du simulateur au format convenu (``sorties/FORMAT.md``).

Quatre tables qui imitent les tables administratives réelles — ``rpu``, ``rsa``,
``rum``, ``mouvements`` — avec une colonne ``type = SIM`` et des colonnes ``sim_``
(la vérité du simulateur), plus la série quotidienne ``tension`` et un
``resume.yaml``. Seule la **période utile** est écrite : les venues arrivées entre
le début et la fin de la période, les séjours commencés dans la période, et les RUM
et mouvements de ces séjours ; la chauffe et la marge de fin servent seulement à
mettre l'hôpital en régime et à laisser finir les séjours.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import yaml

from .moteur import Simulation

SEXE_RPU = {"M": "1", "F": "2"}


def _minute(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s).dt.floor("min")


def tables(sim: Simulation) -> dict[str, pd.DataFrame]:
    """Les tables de sortie, période utile seulement, dans l'ordre des colonnes de FORMAT.md."""
    p = sim.p
    st = p.structure
    nohop = str(st.urgences.get("nohop") or st.etablissement)
    finess = str(st.urgences.get("finess") or f"FINESS_{st.etablissement}")
    debut, fin = sim.debut, sim.debut + pd.Timedelta(days=sim.duree_jours)
    j = sim.journal.tableaux()
    v, s, r, m, t = j["venues"], j["sejours"], j["rums"], j["mouvements"], j["tension"]

    v = v[(v["entree"] >= debut) & (v["entree"] < fin) & v["sortie"].notna()].copy()
    rpu = pd.DataFrame({
        "type": "SIM", "finess": finess, "nohop": nohop, "no_visite": v["no_visite"].astype(int), "ipp": v["ipp"].astype(int),
        "entree": _minute(v["entree"]), "sortie": _minute(v["sortie"]), "age": v["age"].astype(int), "sexe": v["sexe"].map(SEXE_RPU),
        "gravite": v["gravite"], "mode_entree": "8", "mode_sortie": v["mode_sortie"], "nas": v["nas"].astype("Int64"),
        "sim_situation": v["situation"], "sim_classe_tension": v["classe_tension"], "sim_service_prevu": v["service_prevu"],
        "sim_rang": v["rang"].astype("Int64"), "sim_attente_lit_h": v["attente_lit_h"].round(2),
        "sim_uhcd": v["uhcd"].map({True: "oui", False: "non"}), "sim_brancard": v["brancard"].map({True: "oui", False: "non"}),
        "sim_regle": v["regle"],
    })

    s = s[(s["d8eeue"] >= debut) & (s["d8eeue"] < fin)].copy()
    rsa = pd.DataFrame({
        "type": "SIM", "nohop": nohop, "nas": s["nas"].astype(int), "ipp": s["ipp"].astype(int), "no_visite": s["no_visite"].astype("Int64"),
        "agean": s["agean"].astype(int), "sexe": s["sexe"].map(SEXE_RPU), "ghm": s["ghm"], "gpcompx": s["gpcompx"],
        "d8eeue": _minute(s["d8eeue"]), "d8soue": _minute(s["d8soue"]), "duree": s["duree"].astype(int),
        "echpmsi": s["echpmsi"], "prov": s["prov"], "cdpu": s["cdpu"], "schpmsi": s["schpmsi"], "dest": s["dest"], "nb_rum": s["nb_rum"].astype(int),
        "sim_origine": s["sim_origine"], "sim_mono_uhcd": s["sim_mono_uhcd"], "sim_soins_critiques": s["sim_soins_critiques"],
        "sim_reports": s["sim_reports"].astype(int), "sim_classe_age": s["sim_classe_age"], "sim_racine": s["sim_racine"],
        "sim_duree_tiree_j": s["sim_duree_tiree_j"], "sim_mode_sortie": s["sim_mode_sortie"],
    })

    r = r[r["nas"].isin(rsa["nas"])].merge(rsa[["nas", "agean", "ghm"]], on="nas", how="left").sort_values(["nas", "norum"])
    libelles = {c: sv.libelle for c, sv in st.services.items()}
    typaut = {c: ("07A" if c == st.service_urgences.code else ("01A" if "rea" in sv.soins_critiques else "02A" if "usi" in sv.soins_critiques
                                                                  else "03A" if "usc" in sv.soins_critiques else "")) for c, sv in st.services.items()}
    rum = pd.DataFrame({
        "type": "SIM", "nohop": nohop, "nas": r["nas"].astype(int), "ipp": r["ipp"].astype(int), "norum": r["norum"].astype(int), "noseqrum": r["noseqrum"].astype(int),
        "spe_uma": r["spe_uma"], "lib_spe_uma": r["spe_uma"].map(libelles), "typaut": r["spe_uma"].map(typaut), "cdurm": r["spe_uma"],
        "d8eeue": _minute(r["d8eeue"]), "d8soue": _minute(r["d8soue"]), "mdeeue": r["mdeeue"], "cdpu": r["cdpu"], "mdsoue": r["mdsoue"],
        "agean": r["agean"].astype(int), "ghm": r["ghm"],
        "sim_service_prevu": r["sim_service_prevu"], "sim_rang": r["sim_rang"].astype("Int64"),
        "sim_heberge": ((r["sim_service_prevu"] != "") & (r["sim_service_prevu"] != r["spe_uma"])).map({True: "oui", False: "non"}),
        "sim_regle": r["sim_regle"], "sim_attente_h": r["sim_attente_h"].round(2), "sim_supplement_h": r["sim_supplement_h"].round(2),
    })

    m = m[m["nas"].isin(rsa["nas"])].sort_values(["nas", "nomvt"])
    mouvements = pd.DataFrame({
        "type": "SIM", "nohop": nohop, "nas": m["nas"].astype(int), "ipp": m["ipp"].astype(int), "nomvt": m["nomvt"].astype(int), "nbmvt": m["nbmvt"].astype(int),
        "spe_uma": m["spe_uma"], "d8eeue": _minute(m["d8eeue"]), "d8soue": _minute(m["d8soue"]), "prov": m["prov"], "lit": m["lit"].astype(int), "sim_type": m["sim_type"],
    })

    tension = t[(t["date"] >= debut) & (t["date"] < fin)].copy()
    return {"rpu": rpu.reset_index(drop=True), "rsa": rsa.reset_index(drop=True), "rum": rum.reset_index(drop=True),
            "mouvements": mouvements.reset_index(drop=True), "tension": tension.reset_index(drop=True)}


def resume(sim: Simulation, tabl: dict[str, pd.DataFrame]) -> dict:
    c = sim.compteurs
    return {
        "etablissement": sim.p.structure.etablissement,
        "dossier_parametres": str(sim.p.dossier),
        "graine": int(sim.graine),
        "periode": {"debut": str(sim.debut.date()), "fin": str((sim.debut + pd.Timedelta(days=sim.duree_jours)).date()),
                    "duree_jours": sim.duree_jours, "chauffe_jours": sim.chauffe_jours, "marge_fin_jours": sim.marge_fin_jours},
        "saison": sim.saison,
        "capacites": {code: l.capacite for code, l in sim.parc.lits.items()},
        "volumes": {k: int(len(t)) for k, t in tabl.items()},
        "compteurs": {"transferts_faute_de_service": c.transferts_faute_de_service, "transferts_faute_de_lit": c.transferts_faute_de_lit,
                      "brancards": c.brancards, "mutations_abandonnees": c.mutations_abandonnees, "reports": len(sim.journal.reports)},
    }


def ecrire(sim: Simulation, dossier: str | Path, csv: bool = True) -> dict[str, pd.DataFrame]:
    """Écrit les tables dans ``dossier`` (parquet, et CSV si demandé), ``tension.parquet`` et ``resume.yaml``."""
    dossier = Path(dossier)
    dossier.mkdir(parents=True, exist_ok=True)
    tabl = tables(sim)
    for nom, t in tabl.items():
        t.to_parquet(dossier / f"{nom}.parquet", index=False)
        if csv and nom != "tension":
            t.to_csv(dossier / f"{nom}.csv", index=False, date_format="%Y-%m-%d %H:%M")
    with open(dossier / "resume.yaml", "w", encoding="utf-8") as f:
        yaml.safe_dump(resume(sim, tabl), f, allow_unicode=True, sort_keys=False)
    return tabl
