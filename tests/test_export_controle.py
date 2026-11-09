"""Export au format FORMAT.md et tableau de contrôle, sur une simulation courte de l'hôpital fictif."""
import os
from pathlib import Path

import pandas as pd
import pytest
import yaml

from gen_admissions.simulateur.controle import bilan, tableau_controle
from gen_admissions.simulateur.export import ecrire, tables
from gen_admissions.simulateur.moteur import Simulation
from gen_admissions.simulateur.parametres import Parametres

DOSSIER = Path(os.environ.get("GEN_ADMISSIONS_PARAMETRES", Path(__file__).resolve().parents[1] / "parametres" / "hopital_type"))
COLONNES = {
    "rpu": ["type", "finess", "nohop", "no_visite", "ipp", "entree", "sortie", "age", "sexe", "gravite", "mode_entree", "mode_sortie", "nas"],
    "rsa": ["type", "nohop", "nas", "ipp", "no_visite", "agean", "sexe", "ghm", "gpcompx", "d8eeue", "d8soue", "duree", "echpmsi", "prov", "cdpu", "schpmsi", "dest", "nb_rum"],
    "rum": ["type", "nohop", "nas", "ipp", "norum", "noseqrum", "spe_uma", "lib_spe_uma", "typaut", "cdurm", "d8eeue", "d8soue", "mdeeue", "cdpu", "mdsoue", "agean", "ghm"],
    "mouvements": ["type", "nohop", "nas", "ipp", "nomvt", "nbmvt", "spe_uma", "d8eeue", "d8soue", "prov", "lit", "sim_type"],
}


@pytest.fixture(scope="module")
def sim():
    p = Parametres.charger(DOSSIER, verifier=False)
    return Simulation(p, graine=11, duree_jours=60, chauffe_jours=20, marge_fin_jours=10).lancer()


@pytest.fixture(scope="module")
def tabl(sim, tmp_path_factory):
    return ecrire(sim, tmp_path_factory.mktemp("sortie"))


def test_format(sim, tabl):
    for nom, colonnes in COLONNES.items():
        t = tabl[nom]
        assert list(t.columns)[:len(colonnes)] == colonnes, nom
        assert (t["type"] == "SIM").all()
    rpu, rsa, rum, m = tabl["rpu"], tabl["rsa"], tabl["rum"], tabl["mouvements"]
    debut, fin = sim.debut, sim.debut + pd.Timedelta(days=sim.duree_jours)
    assert rpu["entree"].between(debut, fin).all() and rsa["d8eeue"].between(debut, fin).all()
    assert set(rpu["sexe"]) <= {"1", "2"} and set(rpu["mode_sortie"]) <= {"6", "7", "8", "9"}
    assert rsa["nas"].is_unique and rum["nas"].isin(rsa["nas"]).all() and m["nas"].isin(rsa["nas"]).all()
    assert (rum.groupby("nas")["norum"].max() == rsa.set_index("nas")["nb_rum"].reindex(rum.groupby("nas")["norum"].max().index)).all()
    assert rum.groupby("nas")["noseqrum"].nunique().eq(1).all()
    assert (rum.loc[rum["spe_uma"] == "URG", "typaut"] == "07A").all()
    # mono-UHCD : un RUM d'UHCD, cdpu 5 ; hospitalisés venus des urgences : premier RUM cdpu 5, les suivants vides
    premiers = rum[rum["norum"] == 1]
    assert (premiers.merge(rsa[["nas", "sim_origine"]], on="nas").query("sim_origine == 'urgences'")["cdpu"] == "5").all()
    assert (rum.loc[rum["norum"] > 1, "cdpu"] == "").all()


def test_fichiers_ecrits(sim, tabl, tmp_path_factory):
    dossier = tmp_path_factory.mktemp("sortie2")
    ecrire(sim, dossier)
    noms = {f.name for f in dossier.iterdir()}
    assert {"rpu.parquet", "rsa.parquet", "rum.parquet", "mouvements.parquet", "tension.parquet", "resume.yaml", "rpu.csv", "rsa.csv"} <= noms
    r = yaml.safe_load(open(dossier / "resume.yaml", encoding="utf-8"))
    assert r["graine"] == 11 and r["volumes"]["rpu"] == len(tabl["rpu"])


def test_controle(sim, tabl):
    t = tableau_controle(tabl, sim.p, sim.duree_jours)
    assert {"presents_moyens", "dms", "part_urgences", "part_hospitalisation", "tension_moyenne"} <= set(t["indicateur"])
    assert t["simule"].notna().sum() > 30
    avec = t[t["dans_fourchette"].notna()]
    assert len(avec) > 30 and avec["dans_fourchette"].astype(bool).any()   # la fourchette elle-même se teste sur un an (ligne de commande)
    assert ((avec["simule"] >= avec["min"]) & (avec["simule"] <= avec["max"])).equals(avec["dans_fourchette"].astype(bool))
    assert "indicateurs sur" in bilan(t)
