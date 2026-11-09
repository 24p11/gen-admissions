"""Les lits et la règle D, sur un hôpital miniature construit à la main."""
import numpy as np
import pandas as pd
import pytest
import simpy

from gen_admissions.simulateur.lits import Lits, ParcDeLits, PRIORITES_DEFAUT
from gen_admissions.simulateur.parametres import Service, Structure, TableTirage


class Patient:
    def __init__(self, nom, origine="urgences"):
        self.nom, self.origine = nom, origine

    def __repr__(self):
        return self.nom


def structure_mini():
    services = {
        "CARDIO": Service("CARDIO", "Cardiologie", "CARDIO", "medecine", lits=2),
        "MED_INT": Service("MED_INT", "Médecine interne", "MED_INT", "medecine", lits=1, lits_ouverts=1),
        "GERIA": Service("GERIA", "Gériatrie", "GERIA", "medecine", lits=1),
        "REA": Service("REA", "Réanimation", "REA", "soins_critiques", lits=1, soins_critiques=frozenset({"rea", "usc"})),
    }
    return Structure(etablissement="MINI", description="", debut=pd.Timestamp("2026-01-01"), classes_age=["0-17", "18-64", "65-74", "75+"],
                     classes_age_duree=["0-59", "60+"], services=services, urgences={"service": "URG", "delai_transfert_h": 24},
                     lits={"delai_report_programme_h": 8, "priorites": dict(PRIORITES_DEFAUT)}, sorties={}, population={}, simulation={},
                     autorisations_soins_critiques={})


class ParamsMini:
    """Le strict nécessaire pour ParcDeLits : la structure et une table de supplément."""
    def __init__(self):
        self.structure = structure_mini()
        self.supplement = TableTirage(pd.DataFrame({"classe_tension": ["basse", "haute"], "tranche_h": ["2-4", "6-12"], "part": [1.0, 1.0]}),
                                      ["classe_tension"], ["tranche_h"])


CANDIDATS = [("CARDIO", 0.7, 1), ("MED_INT", 0.2, 1), ("GERIA", 0.1, 2)]


def parc():
    env = simpy.Environment()
    return env, ParcDeLits(env, ParamsMini(), np.random.default_rng(0))


def test_capacites_et_comptages():
    env, parc_ = parc()
    assert parc_.lits["CARDIO"].capacite == 2 and parc_.lits["MED_INT"].capacite == 1
    l = parc_.lits["CARDIO"]
    req = l.demander(1); env.run(); occ = l.installer(req, Patient("a"))
    assert (l.occupes, l.libres, occ.lit) == (1, 1, 1)
    l.liberer(occ)
    assert (l.occupes, l.libres) == (0, 2)


def test_rang1_immediat_puis_redirection_rang1():
    env, parc_ = parc()
    resultats = []

    def demande(nom, t):
        yield env.timeout(t)
        r = yield env.process(parc_.obtenir_lit(Patient(nom), CANDIDATS, "CARDIO", "urgences", "basse"))
        resultats.append((nom, r))

    for i, t in enumerate([0, 0, 0]):            # trois patients à t=0 : CARDIO a 2 lits, le 3e va en MED_INT (rang 1)
        env.process(demande(f"p{i}", t))
    env.run()
    services = [r.service for _, r in resultats]
    assert services.count("CARDIO") == 2 and services.count("MED_INT") == 1
    assert all(r.issue == "lit" and r.attente_h == 0 and r.rang == 1 for _, r in resultats)
    assert {r.regle for _, r in resultats} == {"prevu", "rang1"}


def test_rang2_apres_supplement():
    env, parc_ = parc()
    # on remplit CARDIO (2) et MED_INT (1) ; le 4e patient doit attendre le supplément (2-4 h en tension basse) puis aller en GERIA (rang 2)
    occupants = []
    for s in ["CARDIO", "CARDIO", "MED_INT"]:
        req = parc_.lits[s].demander(1); env.run(); occupants.append(parc_.lits[s].installer(req, Patient("x")))
    resultat = {}

    def demande():
        r = yield env.process(parc_.obtenir_lit(Patient("p"), CANDIDATS, "CARDIO", "urgences", "basse"))
        resultat.update(vars(r))
    env.process(demande()); env.run()
    assert resultat["issue"] == "lit" and resultat["service"] == "GERIA" and resultat["rang"] == 2
    assert 2 <= resultat["attente_h"] <= 4 and resultat["regle"] == "rang2"


def test_lit_rang1_libere_pendant_le_supplement():
    env, parc_ = parc()
    occupants = {}
    for s in ["CARDIO", "CARDIO", "MED_INT"]:
        req = parc_.lits[s].demander(1); env.run(); occupants.setdefault(s, []).append(parc_.lits[s].installer(req, Patient("x")))
    resultat = {}

    def demande():
        r = yield env.process(parc_.obtenir_lit(Patient("p"), CANDIDATS, "CARDIO", "urgences", "haute"))   # supplément 6-12 h
        resultat.update(vars(r))

    def liberation():
        yield env.timeout(1.0)
        parc_.lits["CARDIO"].liberer(occupants["CARDIO"][0])
    env.process(demande()); env.process(liberation()); env.run()
    assert resultat["service"] == "CARDIO" and resultat["rang"] == 1 and abs(resultat["attente_h"] - 1.0) < 1e-9
    assert parc_.lits["GERIA"].en_attente == 0 and parc_.lits["MED_INT"].en_attente == 0   # les autres demandes ont été retirées


def test_dernier_recours_puis_transfert():
    env, parc_ = parc()
    for s in ["CARDIO", "CARDIO", "MED_INT", "GERIA"]:
        req = parc_.lits[s].demander(1); env.run(); parc_.lits[s].installer(req, Patient("x"))
    resultat = {}

    def demande():
        r = yield env.process(parc_.obtenir_lit(Patient("p"), CANDIDATS, "CARDIO", "urgences", "basse"))
        resultat.update(vars(r))
    env.process(demande()); env.run()
    assert resultat["issue"] == "transfert" and abs(resultat["attente_h"] - 24) < 1e-9 and resultat["regle"] == "delai_depasse"
    assert all(l.en_attente == 0 for l in parc_.lits.values())


def test_report_programme_et_priorites():
    env, parc_ = parc()
    occ = []
    for s in ["CARDIO", "CARDIO"]:
        req = parc_.lits[s].demander(1); env.run(); occ.append(parc_.lits[s].installer(req, Patient("x")))
    ordre = []

    def demande(nom, origine, t):
        yield env.timeout(t)
        r = yield env.process(parc_.obtenir_lit(Patient(nom, origine), [("CARDIO", 1.0, 1)], "CARDIO", origine, "basse"))
        ordre.append((nom, r.issue, r.service, round(r.attente_h, 2)))

    def liberations():
        yield env.timeout(2.0); parc_.lits["CARDIO"].liberer(occ[0])
        yield env.timeout(10.0); parc_.lits["CARDIO"].liberer(occ[1])

    env.process(demande("prog", "programme", 0.0))      # arrivé le premier, mais priorité 2
    env.process(demande("urg", "urgences", 0.5))        # arrivé après, priorité 1 : servi en premier
    env.process(liberations()); env.run()
    d = {n: (i, s, a) for n, i, s, a in ordre}
    assert d["urg"] == ("lit", "CARDIO", 1.5)
    assert d["prog"][0] == "report" and d["prog"][2] == 8.0      # délai de report 8 h, pas de second lit à temps


def test_soins_critiques_sans_redirection():
    env, parc_ = parc()
    req = parc_.lits["REA"].demander(0); env.run(); occ = parc_.lits["REA"].installer(req, Patient("x"))
    resultat = {}

    def demande():
        r = yield env.process(parc_.obtenir_lit(Patient("p"), [("REA", 0.6, 1), ("CARDIO", 0.4, 1)], "REA", "urgences", "basse"))
        resultat.update(vars(r))

    def liberation():
        yield env.timeout(3.0); parc_.lits["REA"].liberer(occ)
    env.process(demande()); env.process(liberation()); env.run()
    assert resultat["service"] == "REA" and resultat["regle"] == "soins_critiques" and abs(resultat["attente_h"] - 3.0) < 1e-9


def test_fermeture_et_reouverture():
    env, parc_ = parc()
    l = parc_.lits["CARDIO"]
    env.process(l.fermer(1)); env.run()
    assert (l.capacite, l.fermes, l.libres, l.occupes) == (2, 1, 1, 0)
    req = l.demander(1); env.run(); occ = l.installer(req, Patient("a"))
    assert l.libres == 0 and l.presents() == 1
    l.rouvrir(1)
    assert l.fermes == 0 and l.libres == 1
    l.liberer(occ)
    assert l.libres == 2
