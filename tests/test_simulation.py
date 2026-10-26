"""Tests du simulateur sur une courte période : cohérence des données produites."""

from pathlib import Path

import pandas as pd
import pytest

from gen_admissions import config, controle, export, moteur

DOSSIER = Path(__file__).resolve().parents[1] / "parametres" / "hopital_type"


@pytest.fixture(scope="module")
def resultat():
    p = config.charger(DOSSIER)
    return moteur.simuler(p, graine=7, duree_jours=30, chauffe_jours=10, marge_fin_jours=30)


@pytest.fixture(scope="module")
def tables(resultat):
    return export.tables(resultat)


def test_aucun_service_ne_depasse_sa_capacite(resultat):
    occ = controle.occupation_horaire(resultat)
    for s in resultat.parametres.services_avec_lits:
        assert occ[s].max() <= resultat.parametres.services[s].lits, s
        assert occ[s].min() >= 0


def test_les_rum_d_un_sejour_se_suivent_sans_trou(resultat):
    for sejour in resultat.sejours:
        if sejour.sortie is None:
            continue
        assert sejour.rums[0].entree == sejour.entree
        for a, b in zip(sejour.rums, sejour.rums[1:]):
            assert a.sortie == b.entree and a.mode_sortie == "mutation" and b.mode_entree == "mutation"
        assert sejour.rums[-1].sortie == sejour.sortie and sejour.rums[-1].mode_sortie == sejour.mode_sortie


def test_un_patient_n_est_jamais_dans_deux_sejours_en_meme_temps(resultat):
    par_patient = {}
    for s in resultat.sejours:
        if s.sortie is not None:
            par_patient.setdefault(s.patient.id, []).append((s.entree, s.sortie))
    for intervalles in par_patient.values():
        intervalles.sort()
        for (_, fin), (debut, _) in zip(intervalles, intervalles[1:]):
            assert debut >= fin


def test_tables_coherentes(tables):
    rpu, rss, rum, mvt = tables["rpu"], tables["rss"], tables["rum"], tables["mouvements"]
    assert len(rpu) > 100 and len(rss) > 50
    # un passage hospitalisé a un séjour, et réciproquement pour les séjours venus des urgences
    hospitalises = rpu[rpu["mode_sortie"] == "mutation"]
    assert (hospitalises["id_sejour"] != "").all()
    assert set(rss.loc[rss["provenance"] == "5", "id_passage"]) <= set(hospitalises["id_passage"])
    # nombre de RUM cohérent
    assert rum.groupby("id_sejour").size().reindex(rss["id_sejour"]).tolist() == rss["nb_rum"].tolist()
    # codes PMSI
    assert set(rum["mode_sortie"].unique()) <= {"6", "7", "8", "9", ""}
    assert set(rum["mode_entree"].unique()) <= {"6", "8"}
    # mouvements : une entrée et au plus une sortie par séjour
    assert (mvt[mvt["type"] == "entree"].groupby("id_sejour").size() == 1).all()
    assert (mvt[mvt["type"] == "sortie"].groupby("id_sejour").size() <= 1).all()
    # les hébergements sont tracés dans la vérité de référence
    verite = tables["verite_sejours"]
    assert (verite["nb_hebergements"] == rum.groupby("id_sejour")["heberge"].apply(lambda s: (s == "oui").sum()).reindex(verite["id_sejour"]).values).all()


def test_reproductible():
    p = config.charger(DOSSIER)
    a = export.tables(moteur.simuler(p, graine=3, duree_jours=10, chauffe_jours=3, marge_fin_jours=10))
    b = export.tables(moteur.simuler(p, graine=3, duree_jours=10, chauffe_jours=3, marge_fin_jours=10))
    pd.testing.assert_frame_equal(a["rum"], b["rum"])
    pd.testing.assert_frame_equal(a["rpu"], b["rpu"])
