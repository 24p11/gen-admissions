"""Tests des briques de base : hasard, distributions, extraction, chargement des paramètres."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from gen_admissions import config, distributions, extraction
from gen_admissions.hasard import FLUX, Hasard

DOSSIER = Path(__file__).resolve().parents[1] / "parametres" / "hopital_type"


def test_hasard_reproductible_et_flux_independants():
    a, b = Hasard(42), Hasard(42)
    assert a["durees"].random(5).tolist() == b["durees"].random(5).tolist()
    # modifier les tirages d'un flux ne change pas les autres
    c, d = Hasard(42), Hasard(42)
    c["parcours"].random(1000)
    assert c["durees"].random(5).tolist() == d["durees"].random(5).tolist()
    assert len(set(FLUX)) == len(FLUX)


def test_lognormale_retrouve_les_quartiles():
    loi = distributions.LoiDuree.depuis_quartiles(mediane=5, q1=3, q3=9, part_moins_1j=0.0)
    rng = np.random.default_rng(0)
    tirages = np.array([loi.tirer_heures(rng) for _ in range(20000)]) / 24.0
    q1, med, q3 = np.quantile(tirages, [0.25, 0.5, 0.75])
    assert abs(med - 5) < 0.2 and abs(q1 - 3) < 0.2 and abs(q3 - 9) < 0.4


def test_extraction_fusionne_et_neutralise_les_hebergements():
    etapes = pd.DataFrame([
        # séjour 1 : hébergé en PNEUMO pour le compte de CARDIO, puis CARDIO, puis REA, puis CARDIO
        dict(id_sejour=1, rang=1, specialite="PNEUMO", specialite_prevue="CARDIO", heberge=True, duree_j=1.0),
        dict(id_sejour=1, rang=2, specialite="CARDIO", specialite_prevue="CARDIO", heberge=False, duree_j=2.0),
        dict(id_sejour=1, rang=3, specialite="REA", specialite_prevue="REA", heberge=False, duree_j=3.0),
        dict(id_sejour=1, rang=4, specialite="CARDIO", specialite_prevue="CARDIO", heberge=False, duree_j=5.0),
        dict(id_sejour=2, rang=1, specialite="CARDIO", specialite_prevue="CARDIO", heberge=False, duree_j=0.5),
    ])
    for c, v in dict(mode_entree="urgences", classe_age="65-79", sexe="M", mode_sortie="domicile").items():
        etapes[c] = v
    fusion = extraction.fusionner_etapes(etapes)
    traj = extraction.trajectoires(fusion)
    assert traj.set_index("id_sejour").loc[1, "trajectoire"] == "CARDIO > REA > CARDIO"
    assert fusion[(fusion.id_sejour == 1) & (fusion.rang == 1)]["duree_j"].item() == 3.0
    cat = extraction.catalogue(traj, seuil=1)
    assert cat["effectif"].sum() == 2
    tp = extraction.table_passage(traj, seuil=1)
    sommes = tp.groupby(["niveau", "service", "precedent", "classe_age", "mode_entree"])["probabilite"].sum()
    assert np.allclose(sommes, 1.0)
    dur = extraction.durees(fusion, seuil=1)
    ligne = dur[(dur.niveau == "service") & (dur.service == "CARDIO")].iloc[0]
    assert ligne["part_moins_1j"] == pytest.approx(1 / 3, abs=1e-3)


def test_chargement_des_parametres():
    p = config.charger(DOSSIER)
    assert "CARDIO" in p.services and p.services["CARDIO"].lits > 0
    assert p.classe_age(72) == "65-79" and p.classe_age(85) == "80+" and p.classe_age(3) == "0-17"
    assert not p.services["PED"].accepte(30, "M") and p.services["PED"].accepte(5, "F")
    assert not p.services["GYN_OBS"].accepte(30, "M")
    for niveau, cases in p.table_passage.items():
        for cle, (suites, probas, eff) in cases.items():
            assert abs(probas.sum() - 1) < 1e-6, (niveau, cle)
