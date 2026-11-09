"""Les tirages, sur l'hôpital fictif : des milliers de patients doivent reproduire les tables."""
import os
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from gen_admissions.simulateur.parametres import Parametres
from gen_admissions.simulateur.tirages import SITUATIONS_AVEC_SEJOUR, Tireur

DOSSIER = Path(os.environ.get("GEN_ADMISSIONS_PARAMETRES", Path(__file__).resolve().parents[1] / "parametres" / "hopital_type"))
FICTIF = "GEN_ADMISSIONS_PARAMETRES" not in os.environ


@pytest.fixture(scope="module")
def p():
    return Parametres.charger(DOSSIER, verifier=False)


@pytest.fixture(scope="module")
def venues(p):
    t = Tireur(p, np.random.default_rng(0))
    return t, [t.venue_urgences(p.saison, mois=1, classe_tension="moyenne") for _ in range(20_000)]


def test_patient_coherent(venues):
    t, vs = venues
    for v in vs[:2000]:
        assert v.classe_age in t.p.structure.classes_age and v.sexe in ("M", "F")
        assert v.duree_urgences_h > 0
        if v.situation in SITUATIONS_AVEC_SEJOUR:
            assert v.ghm.startswith(v.racine_ghm) and len(v.ghm) == 6 and v.duree_sejour_j > 0
            assert set(v.soins_critiques) == {"rea", "usi", "usc"} and v.mode_sortie
            assert v.classe_age_duree in t.p.structure.classes_age_duree
        else:
            assert v.racine_ghm == "" and v.ghm == ""
        if v.situation == "mono_uhcd":
            assert v.duree_urgences_h >= t.seuil_uhcd_h and abs(v.duree_sejour_j * 24 - v.duree_urgences_h) < 1e-9
        if v.situation == "hospitalisation":
            assert v.transfert or (v.service_prevu and v.service_prevu in t.p.structure.services)
            if not v.transfert:
                assert abs(sum(c[1] for c in v.services_candidats) - 1) < 1e-9
                assert all(r in (1, 2) for _, _, r in v.services_candidats)
                assert v.service_prevu in [c[0] for c in v.services_candidats]


def test_situations_reproduisent_la_table(venues):
    t, vs = venues
    p = t.p
    # part attendue de chaque situation = somme sur les classes d'âge de P(âge) × P(situation | âge, mois 1)
    ligne_patients = p.patients.chercher()
    poids_age = pd.Series(ligne_patients.parts, index=[c[0] for c in ligne_patients.categories]).groupby(level=0).sum()
    attendu = Counter()
    for ca, w in poids_age.items():
        ligne = p.venues.chercher(saison=p.saison, periode=1, classe_age=ca)
        for (situation, _, _), part in zip(ligne.categories, ligne.parts):
            attendu[situation] += w * part
    observe = pd.Series([v.situation for v in vs]).value_counts(normalize=True)
    for situation, part in attendu.items():
        assert abs(observe.get(situation, 0) - part) < 0.01, (situation, observe.get(situation, 0), part)


def test_gradient_d_age(venues):
    t, vs = venues
    df = pd.DataFrame({"classe_age": [v.classe_age for v in vs], "hosp": [v.situation == "hospitalisation" for v in vs]})
    taux = df.groupby("classe_age")["hosp"].mean()
    assert taux["75+"] > taux["18-64"]


def test_tension_allonge_les_passages(p):
    t = Tireur(p, np.random.default_rng(1))
    def duree_moyenne(classe):
        d = [t.venue_urgences(p.saison, 1, classe) for _ in range(6000)]
        return np.mean([v.duree_urgences_h for v in d if v.situation == "hospitalisation" and v.classe_age == "75+"])
    assert duree_moyenne("haute") > duree_moyenne("basse")


def test_premier_service_reproduit_les_rangs(venues):
    t, vs = venues
    hosp = [v for v in vs if v.situation == "hospitalisation" and not v.transfert]
    assert len(hosp) > 500
    rangs = Counter(next(r for s, _, r in v.services_candidats if s == v.service_prevu) for v in hosp)
    part_rang2 = rangs[2] / len(hosp)
    assert 0.0 < part_rang2 < 0.4, rangs
    transferts = sum(v.transfert for v in vs if v.situation == "hospitalisation") / max(1, sum(v.situation == "hospitalisation" for v in vs))
    assert transferts < 0.1
    # les contraintes d'âge des services sont respectées
    for v in hosp:
        assert t.p.structure.services[v.service_prevu].accepte(v.classe_age, v.sexe)


def test_entree_programmee(p):
    t = Tireur(p, np.random.default_rng(2))
    for s in p.structure.conventionnels:
        if p.casemix.chercher(specialite=s.code) is None:
            continue
        pts = [t.entree_programmee(s.code) for _ in range(300)]
        for pt in pts:
            assert pt.origine == "programme" and pt.service_prevu == s.code and pt.ghm and pt.duree_sejour_j > 0
            assert s.accepte(pt.classe_age, pt.sexe), (s.code, pt.classe_age, pt.sexe)
        racines = {pt.racine_ghm for pt in pts}
        attendues = {c[1] for c in p.casemix.chercher(specialite=s.code).categories}
        assert racines <= attendues


def test_durees_de_sejour_reproduisent_la_table(p):
    t = Tireur(p, np.random.default_rng(3))
    racine = p.premier_service.blocs(("racine_ghm",))[0][0]
    ghm = racine + "1"
    ligne = p.durees_ghm.chercher(ghm=ghm, classe_age_duree="60+")
    if ligne is None:
        pytest.skip("pas de ligne pour ce GHM")
    attendu = sum(part for (d,), part in zip(ligne.categories, ligne.parts) if d != "60+" and int(d) <= 3)
    tirs = [t.duree_vers_jours(ligne.tirer(t.rng)[0], None) for _ in range(10_000)]
    observe = np.mean([d < 4 for d in tirs])
    assert abs(observe - attendu) < 0.02


def test_suite_parcours_et_supplement(p):
    t = Tireur(p, np.random.default_rng(4))
    s = p.structure.conventionnels[0].code
    suites = Counter(t.suite_parcours(s, "AUCUN", "75+", "urgences")[0] for _ in range(2000))
    assert set(suites) <= set(p.structure.services) | {"sortie"}
    assert suites["sortie"] > 0
    assert all(0 <= t.supplement_rang2_h("haute") for _ in range(100))


def test_casemix_restreint_aux_classes_du_service(p):
    """Un service dont le casemix ne contient aucune classe acceptée se replie sur le casemix global, sans erreur."""
    import copy, warnings
    from gen_admissions.simulateur.tirages import Tireur
    q = copy.copy(p)
    q.structure = copy.deepcopy(p.structure)
    s = q.structure.services["PED"]
    q.structure.services["PED"] = type(s)(**{**s.__dict__, "age_min": 90, "age_max": None})   # une « pédiatrie » qui n'accepte que les 90 ans et plus
    t = Tireur(q, np.random.default_rng(3))
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        pat = t.entree_programmee("PED")
    assert pat.classe_age == "75+" and pat.age >= 90
    assert any("repli" in str(x.message) for x in w)
    # et un service ordinaire garde son casemix, avec l'âge borné par age_min
    s2 = q.structure.services["GERIA"]
    assert all(t.entree_programmee("GERIA").age >= (s2.age_min or 0) for _ in range(30))
