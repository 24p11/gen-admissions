"""Le module de paramètres, sur l'hôpital fictif (ou sur un dossier réel : GEN_ADMISSIONS_PARAMETRES=chemin)."""
import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from gen_admissions.simulateur.parametres import Parametres, texte

DOSSIER = Path(os.environ.get("GEN_ADMISSIONS_PARAMETRES", Path(__file__).resolve().parents[1] / "parametres" / "hopital_type"))
FICTIF = "GEN_ADMISSIONS_PARAMETRES" not in os.environ


@pytest.fixture(scope="module")
def p():
    return Parametres.charger(DOSSIER)


def test_texte():
    assert texte(1) == "1" and texte("1.0") == "1" and texte(1.0) == "1" and texte("75+") == "75+"
    assert texte(float("nan")) == "" and texte("") == "" and texte("0-17") == "0-17"


def test_chargement_et_resume(p):
    r = p.resume()
    print("\n" + r)
    st = p.structure
    assert st.service_urgences.type == "urgences"
    assert len(st.conventionnels) >= 5 and all(s.presents_vises is not None for s in st.conventionnels)
    assert p.saison in p.saisons
    assert "transférées" in r


def test_contraintes_des_services(p):
    st = p.structure
    ped = [s for s in st.services.values() if s.age_max is not None]
    ger = [s for s in st.services.values() if s.age_min and s.age_min >= 65]
    if ped:
        assert ped[0].accepte("0-17") and not ped[0].accepte("18-64")
    if ger:
        assert ger[0].accepte("75+") and not ger[0].accepte("18-64")
    femmes = [s for s in st.services.values() if s.sexe == "F"]
    if femmes:
        assert femmes[0].accepte("18-64", "F") and not femmes[0].accepte("18-64", "M")


def test_premier_service_restreint(p):
    services = set(p.structure.services)
    racine = "05M09" if FICTIF else p.premier_service.blocs(("racine_ghm",))[0][0]
    ligne = p.premier_service.chercher(racine_ghm=racine, classe_age="75+")
    assert ligne is not None
    specialites = [c[0] for c in ligne.categories]
    assert set(specialites) <= services                        # restreint à la structure
    assert abs(ligne.parts.sum() - 1) < 1e-9                   # renormalisé
    assert set(ligne.annexes["rang"]) <= {1, 2}                # rangs recalculés, pas de rang 3
    if FICTIF:
        assert "CARDIO" in specialites and "NEUROCHIR" not in specialites
        # une classe d'âge inconnue tombe sur le repli par racine seule
        repli = p.premier_service.chercher(racine_ghm=racine, classe_age="inconnue")
        assert repli is not None and repli.niveau == ("racine_ghm",)
        # racines prises en charge seulement par des services absents de l'hôpital : transférées
        assert {"01C04", "05C05"} <= p.racines_transferees
        assert p.premier_service.chercher(racine_ghm="01C04", classe_age="18-64") is None


def test_table_passage_restreinte(p):
    services = set(p.structure.services) | {"sortie"}
    ligne = p.table_passage.chercher(niveau="service", service=p.structure.conventionnels[0].code)
    assert ligne is not None
    assert set(c[0] for c in ligne.categories) <= services
    assert abs(ligne.parts.sum() - 1) < 1e-9
    rangs = ligne.annexes["rang"]
    for (suite,), rang in zip(ligne.categories, rangs):
        assert (suite == "sortie") == pd.isna(rang)


def test_tirage_reproduit_les_parts(p):
    rng = np.random.default_rng(0)
    ligne = p.venues.chercher(saison=p.saison, periode=1, classe_age="75+")
    assert ligne is not None and ligne.niveau == ("saison", "periode", "classe_age")
    n = 20_000
    tirages = [p.venues.tirer(rng, saison=p.saison, periode=1, classe_age="75+") for _ in range(n)]
    situations = pd.Series([t[0] for t in tirages]).value_counts(normalize=True)
    attendu = pd.Series(ligne.parts, index=[c[0] for c in ligne.categories]).groupby(level=0).sum()
    for s, part in attendu.items():
        assert abs(situations.get(s, 0) - part) < 0.01, (s, situations.get(s, 0), part)
    # periode fournie en entier, en texte ou en « 1.0 » : même bloc
    assert p.venues.chercher(saison=p.saison, periode="1.0", classe_age="75+") is ligne


def test_repli_des_durees_urgences(p):
    fin = p.durees_urgences.chercher(situation="hospitalisation", gravite="", classe_age="75+", classe_tension="haute")
    assert fin is not None and "classe_tension" in fin.niveau
    sans_tension = p.durees_urgences.chercher(situation="hospitalisation", gravite="", classe_age="75+", classe_tension="inconnue")
    assert sans_tension is not None and "classe_tension" not in sans_tension.niveau
    assert p.durees_urgences.tirer(np.random.default_rng(1), situation="consultation", gravite="2", classe_age="18-64", classe_tension="basse") in \
        ["0-1", "1-2", "2-4", "4-6", "6-12", "12-24", "24+"]


def test_profils(p):
    semaine, mois = p.arrivees[p.saison]
    assert abs(semaine.poids.sum() - 1) < 1e-9 and abs(sum(semaine.part_jour(j) for j in range(1, 8)) - 1) < 1e-9
    rng = np.random.default_rng(2)
    heures = np.array([semaine.tirer_heure(rng, 1) for _ in range(5000)])
    # la nuit (0-6 h) est creuse par rapport à la matinée (8-14 h)
    assert (heures < 6).mean() < ((heures >= 8) & (heures < 14)).mean()
    assert abs(np.mean([mois.facteur(m) for m in range(1, 13)]) - 1) < 1e-9
    assert p.entrees_programmees.poids.sum() > 0 and p.profil_sorties.poids.sum() > 0


def test_durees_passage_et_soins_critiques(p):
    s = p.structure.conventionnels[0].code
    d = p.durees_passage.chercher(s, "urgences", "75+")
    assert d is not None and d["q1_j"] <= d["mediane_j"] <= d["q3_j"]
    r = p.durees_passage.chercher(s)
    assert r is not None and r["niveau"] == ("specialite",)
    ghm = p.soins_critiques._fin and next(iter(p.soins_critiques._fin))[0]
    pr = p.soins_critiques.probabilites(ghm, "60+")
    assert set(pr) == {"rea", "usi", "usc"} and all(0 <= v <= 1 for v in pr.values())
    assert p.soins_critiques.probabilites("ZZZ999", "60+") == {"rea": 0.0, "usi": 0.0, "usc": 0.0}


def test_classes_tension(p):
    ct = p.classes_tension
    assert ct.classes[0] == "basse" and ct.classes[-1] == "haute"
    assert ct.classe(0) == "basse" and ct.classe(1e9) == "haute"
    double = ct.ajustees(2.0)
    assert double.classe(ct._sup[0] * 1.5) == "basse"


def test_tirages_de_base(p):
    rng = np.random.default_rng(3)
    classe_age, sexe = p.patients.tirer(rng)
    assert classe_age in p.structure.classes_age and sexe in ("M", "F")
    racine = p.premier_service.blocs(("racine_ghm",))[0][0]
    assert p.niveaux.tirer(rng, racine_ghm=racine, classe_age="75+") in list("1234JTZ")
    assert p.sexe_racine.tirer(rng, racine_ghm=racine) in ("M", "F")
    duree = p.durees_ghm.tirer(rng, ghm=racine + "1", classe_age_duree="60+")
    assert duree == "60+" or int(duree) >= 0
