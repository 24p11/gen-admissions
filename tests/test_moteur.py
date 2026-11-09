"""Le moteur, sur l'hôpital fictif : une simulation courte doit tourner et produire un journal cohérent."""
import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from gen_admissions.simulateur.moteur import Simulation
from gen_admissions.simulateur.parametres import Parametres

DOSSIER = Path(os.environ.get("GEN_ADMISSIONS_PARAMETRES", Path(__file__).resolve().parents[1] / "parametres" / "hopital_type"))


@pytest.fixture(scope="module")
def sim():
    p = Parametres.charger(DOSSIER, verifier=False)
    return Simulation(p, graine=7, duree_jours=45, chauffe_jours=25, marge_fin_jours=5).lancer()


@pytest.fixture(scope="module")
def tables(sim):
    return sim.journal.tableaux()


def test_volumes(sim, tables):
    v, s = tables["venues"], tables["sejours"]
    jours = sim.horizon_h / 24
    attendu = float(sim.p.structure.urgences["passages_par_an"]) * jours / 365.25
    assert abs(len(v) / attendu - 1) < 0.15                  # le flux des urgences suit passages_par_an (à la saison près)
    assert len(s) > 0.5 * len(v[v["situation"].isin(["hospitalisation", "mono_uhcd"])])
    assert set(v["situation"]) <= {"consultation", "mono_uhcd", "hospitalisation", "transfert", "deces"}


def test_coherence_des_tables(sim, tables):
    v, s, r, m = tables["venues"], tables["sejours"], tables["rums"], tables["mouvements"]
    # chaque venue hospitalisée terminée a un séjour ; chaque séjour a ses RUM et ses mouvements
    hosp = v[(v["situation"].isin(["hospitalisation", "mono_uhcd"])) & v["nas"].notna()]
    assert hosp["nas"].isin(s["nas"]).mean() > 0.9           # les derniers séjours sont encore en cours à l'horizon
    nb_rum = r.groupby("nas").size()
    termines = s[s["nas"].isin(nb_rum.index)]
    assert (termines.set_index("nas")["nb_rum"] == nb_rum.reindex(termines["nas"]).values).all()
    assert (r["d8soue"] >= r["d8eeue"]).all() and (m["d8soue"] >= m["d8eeue"]).all()
    assert (s["d8soue"] >= s["d8eeue"]).all()
    # les codes du format
    assert set(s["echpmsi"]) == {"8"} and set(s["schpmsi"]) <= {"6", "7", "8", "9"}
    assert set(r["mdeeue"]) <= {"6", "8"} and set(r["cdpu"]) <= {"", "5"}
    assert (m.groupby("nas")["nomvt"].max() == m.groupby("nas")["nbmvt"].first()).all()
    assert m["spe_uma"].isin(sim.p.structure.services).all()


def test_jamais_plus_de_patients_que_de_lits(sim, tables):
    """À chaque instant, un service n'a jamais plus d'occupants que sa capacité : on le vérifie sur les mouvements."""
    m = tables["mouvements"]
    for service, g in m.groupby("spe_uma"):
        cap = sim.p.structure.services[service].capacite
        evenements = pd.concat([pd.Series(1, index=g["d8eeue"]), pd.Series(-1, index=g["d8soue"])]).sort_index()
        # à un même instant, les sorties avant les entrées
        evenements = evenements.groupby(level=0).sum().cumsum()
        assert evenements.max() <= cap, (service, evenements.max(), cap)


def test_presents_proches_des_vises(sim, tables):
    t = tables["tension"]
    utile = t[t["date"] >= sim.debut]
    for s in sim.p.structure.conventionnels:
        if s.presents_vises and f"occupes_{s.code}" in utile.columns:
            obs = utile[f"occupes_{s.code}"].mean()
            assert abs(obs - s.presents_vises) < 0.3 * s.presents_vises + 3, (s.code, obs, s.presents_vises)


def test_tension_et_regles(sim, tables):
    t, v = tables["tension"], tables["venues"]
    assert len(t) == sim.horizon_h // 24 and set(t["classe_tension"]) <= set(sim.p.classes_tension.classes)
    hosp = v[(v["situation"] == "hospitalisation") & (v["mode_sortie"] == "6")]
    assert hosp["regle"].isin(["prevu", "rang1", "rang2", "dernier_recours", "soins_critiques"]).all()
    assert (hosp["attente_lit_h"] >= 0).all() and hosp["attente_lit_h"].mean() < 6
    assert hosp["rang"].isin([1, 2]).all()
    assert v.loc[v["situation"] == "mono_uhcd", "uhcd"].mean() > 0.5
    assert sim.resume()
