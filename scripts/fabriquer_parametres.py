"""Fabrique les fichiers de paramètres d'un hôpital fictif d'environ 480 lits.

Principe : on décrit une « réalité » fictive (règles médicales simples,
volumes, durées), on en tire des dizaines de milliers de séjours fictifs
sans contrainte de lits, puis on applique à ces séjours le MÊME module
d'extraction (``gen_admissions.extraction``) que l'on appliquera plus tard
aux vraies données agrégées. On obtient ainsi des fichiers de paramètres au
format définitif, et un script d'extraction déjà rodé.

Usage :
    python scripts/fabriquer_parametres.py parametres/hopital_type [--graine 7]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gen_admissions import extraction  # noqa: E402

CLASSES_AGE = ["0-17", "18-64", "65-79", "80+"]
SORTIES = ["domicile", "SSR", "EHPAD", "transfert", "deces"]

# ---------------------------------------------------------------------------
# 1. Structure de l'hôpital
# ---------------------------------------------------------------------------
SERVICES = {
    # code: (libellé, type, lits, services d'hébergement compatibles, contraintes)
    "URG": ("Urgences", "urgences", 0, [], {}),
    "UHCD": ("Unité d'hospitalisation de courte durée", "uhcd", 10, ["MED_POLY"], {}),
    "REA": ("Réanimation", "reanimation", 20, [], {}),
    "USC": ("Soins continus", "soins_continus", 12, [], {}),
    "USIC": ("Soins intensifs cardiologiques", "soins_intensifs", 8, [], {}),
    "CARDIO": ("Cardiologie", "medecine", 36, ["MED_INT", "PNEUMO", "MED_POLY"], {}),
    "PNEUMO": ("Pneumologie", "medecine", 30, ["MED_INT", "CARDIO", "MED_POLY"], {}),
    "MED_INT": ("Médecine interne", "medecine", 40, ["PNEUMO", "MED_POLY", "GASTRO"], {}),
    "GERIA": ("Gériatrie aiguë", "medecine", 40, ["MED_INT", "MED_POLY"], {"age_min": 75}),
    "NEURO": ("Neurologie", "medecine", 30, ["MED_INT", "MED_POLY"], {}),
    "GASTRO": ("Gastro-entérologie", "medecine", 28, ["MED_INT", "CHIR_DIG"], {}),
    "ONCO": ("Oncologie-hématologie", "medecine", 30, ["MED_INT"], {}),
    "MED_POLY": ("Médecine polyvalente post-urgences", "medecine", 40, ["MED_INT", "GERIA", "PNEUMO"], {}),
    "ORTHO": ("Orthopédie-traumatologie", "chirurgie", 36, ["CHIR_DIG", "URO"], {}),
    "CHIR_DIG": ("Chirurgie digestive", "chirurgie", 32, ["ORTHO", "URO"], {}),
    "URO": ("Urologie", "chirurgie", 22, ["CHIR_DIG", "ORTHO"], {}),
    "GYN_OBS": ("Gynécologie-obstétrique", "obstetrique", 34, [], {"sexe": "F"}),
    "PED": ("Pédiatrie", "pediatrie", 28, [], {"age_max": 17}),
}
HEBERGEABLES = [s for s, v in SERVICES.items() if v[2] > 0]

# Taux d'occupation visés (hors urgences). Les unités de soins critiques et
# l'UHCD sont surtout alimentées par les urgences et les mutations : leur
# cible est indicative, elle n'est pas ajustée par les entrées programmées.
OCCUPATION_CIBLE = {
    "UHCD": 0.72, "REA": 0.82, "USC": 0.80, "USIC": 0.78,
    "CARDIO": 0.88, "PNEUMO": 0.88, "MED_INT": 0.90, "GERIA": 0.88, "NEURO": 0.88,
    "GASTRO": 0.85, "ONCO": 0.85, "MED_POLY": 0.92,
    "ORTHO": 0.82, "CHIR_DIG": 0.82, "URO": 0.78, "GYN_OBS": 0.75, "PED": 0.70,
}

# ---------------------------------------------------------------------------
# 2. Urgences
# ---------------------------------------------------------------------------
PASSAGES_URGENCES_PAR_AN = 45_000

PROFIL_HEURE_URGENCES = {  # poids relatif de chaque heure d'arrivée
    0: 2.4, 1: 1.9, 2: 1.5, 3: 1.2, 4: 1.0, 5: 1.0, 6: 1.3, 7: 2.0, 8: 3.4, 9: 5.2, 10: 6.4, 11: 6.8,
    12: 6.0, 13: 5.6, 14: 6.0, 15: 6.2, 16: 6.0, 17: 5.9, 18: 5.8, 19: 5.6, 20: 5.0, 21: 4.3, 22: 3.6, 23: 3.0,
}
PROFIL_JOUR_URGENCES = {"lundi": 1.12, "mardi": 1.02, "mercredi": 0.99, "jeudi": 0.98, "vendredi": 1.00, "samedi": 0.94, "dimanche": 0.95}
PROFIL_MOIS_URGENCES = {1: 1.08, 2: 1.04, 3: 1.03, 4: 0.99, 5: 1.00, 6: 1.00, 7: 0.97, 8: 0.94, 9: 0.98, 10: 0.99, 11: 0.98, 12: 1.06}

# Répartition des passages par classe d'âge et sexe (somme = 1)
PROFIL_AGE_SEXE_URGENCES = {
    ("0-17", "M"): 0.115, ("0-17", "F"): 0.100,
    ("18-64", "M"): 0.255, ("18-64", "F"): 0.250,
    ("65-79", "M"): 0.080, ("65-79", "F"): 0.075,
    ("80+", "M"): 0.050, ("80+", "F"): 0.075,
}
# Gravité CCMU (1 = bénin … 5 = pronostic vital engagé) selon l'âge
GRAVITE_PAR_AGE = {
    "0-17": {1: 0.20, 2: 0.60, 3: 0.17, 4: 0.025, 5: 0.005},
    "18-64": {1: 0.15, 2: 0.58, 3: 0.23, 4: 0.03, 5: 0.01},
    "65-79": {1: 0.05, 2: 0.40, 3: 0.45, 4: 0.08, 5: 0.02},
    "80+": {1: 0.02, 2: 0.30, 3: 0.55, 4: 0.10, 5: 0.03},
}
# Issue du passage selon l'âge et la gravité (domicile, hospitalisation, transfert, deces, parti)
def issue_urgences(classe_age: str, gravite: int) -> dict[str, float]:
    base_hosp = {"0-17": 0.07, "18-64": 0.13, "65-79": 0.36, "80+": 0.52}[classe_age]
    facteur = {1: 0.15, 2: 0.6, 3: 1.6, 4: 3.0, 5: 3.5}[gravite]
    hosp = min(0.92, base_hosp * facteur)
    deces = {1: 0.0, 2: 0.0, 3: 0.001, 4: 0.01, 5: 0.12}[gravite] * (1.6 if classe_age == "80+" else 1.0)
    transfert = {1: 0.005, 2: 0.01, 3: 0.02, 4: 0.04, 5: 0.06}[gravite]
    parti = {1: 0.06, 2: 0.03, 3: 0.01, 4: 0.0, 5: 0.0}[gravite]
    hosp = min(hosp, 1.0 - deces - transfert - parti)
    domicile = max(0.0, 1.0 - hosp - deces - transfert - parti)
    return {"domicile": domicile, "hospitalisation": hosp, "transfert": transfert, "deces": deces, "parti": parti}

# Durée du passage aux urgences (heures) selon l'issue : médiane, q1, q3
DUREE_PASSAGE_URGENCES = {
    "domicile": (3.4, 2.0, 5.5),
    "hospitalisation": (4.5, 3.0, 6.5),   # avant la demande de lit ; l'attente du lit s'ajoute ensuite
    "transfert": (5.0, 3.5, 7.5),
    "deces": (3.0, 1.5, 6.0),
    "parti": (2.0, 1.0, 3.5),
}

# Premier service d'hospitalisation pour un patient venu des urgences, selon âge et sexe
def premier_service_urgences(classe_age: str, sexe: str) -> dict[str, float]:
    if classe_age == "0-17":
        return {"UHCD": 0.15, "PED": 0.78, "ORTHO": 0.03, "CHIR_DIG": 0.025, "REA": 0.015}
    if classe_age == "18-64":
        d = {"UHCD": 0.38, "MED_POLY": 0.07, "CARDIO": 0.06, "PNEUMO": 0.04, "MED_INT": 0.05, "NEURO": 0.05,
             "GASTRO": 0.05, "ORTHO": 0.08, "CHIR_DIG": 0.06, "URO": 0.02, "REA": 0.07, "USIC": 0.04, "ONCO": 0.01}
        if sexe == "F":
            d["GYN_OBS"] = 0.12
            d["URO"] = 0.01
            d["ORTHO"] = 0.08
        return d
    if classe_age == "65-79":
        d = {"UHCD": 0.30, "MED_POLY": 0.11, "CARDIO": 0.09, "PNEUMO": 0.07, "MED_INT": 0.06, "NEURO": 0.06,
             "GASTRO": 0.04, "ORTHO": 0.06, "CHIR_DIG": 0.04, "URO": 0.02, "REA": 0.07, "USIC": 0.06, "ONCO": 0.01, "GERIA": 0.01}
        if sexe == "F":
            d["GYN_OBS"] = 0.01
        return d
    d = {"UHCD": 0.28, "GERIA": 0.14, "MED_POLY": 0.13, "CARDIO": 0.08, "PNEUMO": 0.07, "MED_INT": 0.05, "NEURO": 0.05,
         "GASTRO": 0.03, "ORTHO": 0.07, "CHIR_DIG": 0.02, "URO": 0.01, "REA": 0.02, "USIC": 0.04}
    return d

# ---------------------------------------------------------------------------
# 3. Entrées programmées
# ---------------------------------------------------------------------------
# Valeurs de départ (entrées par semaine) : elles sont ajustées automatiquement
# pour atteindre les taux d'occupation visés.
ENTREES_PROGRAMMEES_SEMAINE = {
    "CARDIO": 25, "PNEUMO": 12, "MED_INT": 18, "GERIA": 6, "NEURO": 14, "GASTRO": 25, "ONCO": 45, "MED_POLY": 3,
    "ORTHO": 45, "CHIR_DIG": 40, "URO": 45, "GYN_OBS": 60, "PED": 25,
}
PROFIL_JOUR_PROGRAMME = {"lundi": 1.7, "mardi": 1.3, "mercredi": 1.1, "jeudi": 1.1, "vendredi": 0.5, "samedi": 0.1, "dimanche": 0.4}
PROFIL_HEURE_PROGRAMME = {7: 4, 8: 6, 9: 4, 10: 2, 11: 1.5, 12: 1, 13: 1.5, 14: 2, 15: 2, 16: 2.5, 17: 3, 18: 2, 19: 1}

def profil_patients_programme(service: str) -> dict[tuple[str, str], float]:
    if service == "PED":
        return {("0-17", "M"): 0.52, ("0-17", "F"): 0.48}
    if service == "GYN_OBS":
        return {("18-64", "F"): 0.92, ("65-79", "F"): 0.06, ("80+", "F"): 0.02}
    if service == "GERIA":
        return {("65-79", "M"): 0.12, ("65-79", "F"): 0.13, ("80+", "M"): 0.30, ("80+", "F"): 0.45}
    age = {
        "CARDIO": (0.0, 0.45, 0.38, 0.17), "PNEUMO": (0.0, 0.45, 0.38, 0.17), "MED_INT": (0.0, 0.55, 0.30, 0.15),
        "NEURO": (0.0, 0.55, 0.32, 0.13), "GASTRO": (0.0, 0.55, 0.33, 0.12), "ONCO": (0.0, 0.50, 0.38, 0.12),
        "MED_POLY": (0.0, 0.40, 0.35, 0.25), "ORTHO": (0.03, 0.55, 0.30, 0.12), "CHIR_DIG": (0.02, 0.60, 0.30, 0.08),
        "URO": (0.02, 0.45, 0.40, 0.13),
    }[service]
    sexe_m = {"URO": 0.72}.get(service, 0.5)
    return {(c, s): p * (sexe_m if s == "M" else 1 - sexe_m) for c, p in zip(CLASSES_AGE, age) for s in "MF" if p > 0}

# ---------------------------------------------------------------------------
# 4. Durées de séjour dans les unités (jours)
# ---------------------------------------------------------------------------
MEDIANE_DUREE = {  # (médiane urgences, médiane programmé), en jours
    "UHCD": (1.0, 1.0), "REA": (4.0, 3.0), "USC": (3.0, 2.0), "USIC": (2.5, 2.0), "CARDIO": (5.0, 3.0),
    "PNEUMO": (7.0, 4.5), "MED_INT": (7.0, 4.5), "GERIA": (10.0, 8.0), "NEURO": (7.0, 4.0), "GASTRO": (5.0, 3.0),
    "ONCO": (7.0, 4.5), "MED_POLY": (6.0, 5.0), "ORTHO": (6.0, 3.5), "CHIR_DIG": (7.0, 4.0), "URO": (4.0, 2.5),
    "GYN_OBS": (3.5, 3.0), "PED": (2.5, 2.0),
}
PART_COURTE = {"UHCD": 0.5, "REA": 0.10, "USC": 0.12, "USIC": 0.12, "GYN_OBS": 0.10, "PED": 0.20}
FACTEUR_AGE = {"0-17": 0.7, "18-64": 0.85, "65-79": 1.0, "80+": 1.25}
SIGMA_DUREE = 0.65

# ---------------------------------------------------------------------------
# 5. Suite du parcours : « réalité » avec mémoire du service d'origine
# ---------------------------------------------------------------------------
MEDECINES = {"CARDIO", "PNEUMO", "MED_INT", "GERIA", "NEURO", "GASTRO", "ONCO", "MED_POLY"}
CHIRURGIES = {"ORTHO", "CHIR_DIG", "URO"}

def suite_reelle(service: str, origine: str | None, classe_age: str, mode_entree: str, visites: set[str]) -> dict[str, float]:
    """Probabilités de la suite du parcours, telles que la « réalité » fictive les définit.

    ``origine`` est le service de spécialité d'où vient le patient avant un
    passage en soins critiques (ce que la table de passage, elle, oubliera).
    """
    age80 = classe_age == "80+"
    age65 = classe_age == "65-79"
    enfant = classe_age == "0-17"
    retour = origine if (origine and origine != service) else None

    if service == "REA":
        d = {"USC": 0.30, "deces": 0.05 if enfant else (0.28 if age80 else 0.17), "transfert": 0.05, "domicile": 0.05}
        d[retour or ("PED" if enfant else "MED_POLY")] = 0.42
        return d
    if service == "USC":
        d = {"REA": 0.08, "domicile": 0.22, "SSR": 0.10 if age80 else 0.04, "deces": 0.06 if age80 else 0.03}
        d[retour or ("PED" if enfant else "MED_INT")] = 0.55
        return d
    if service == "USIC":
        return {"CARDIO": 0.72, "domicile": 0.18, "REA": 0.05, "deces": 0.05}
    if service == "UHCD":
        d = {"domicile": 0.60, "MED_POLY": 0.09, "CARDIO": 0.05, "PNEUMO": 0.05, "MED_INT": 0.04, "NEURO": 0.04,
             "GASTRO": 0.03, "ORTHO": 0.02, "CHIR_DIG": 0.02, "USIC": 0.02, "REA": 0.01, "transfert": 0.02, "deces": 0.01}
        if age80:
            d["GERIA"] = 0.07
            d["domicile"] = 0.53
        if enfant:
            d = {"domicile": 0.75, "PED": 0.23, "transfert": 0.02}
        return d
    if service == "PED":
        return {"domicile": 0.95, "REA": 0.015, "transfert": 0.02, "USC": 0.01, "deces": 0.005}
    if service == "GYN_OBS":
        return {"domicile": 0.965, "REA": 0.01, "USC": 0.015, "transfert": 0.01}
    if service == "GERIA":
        return {"domicile": 0.44, "SSR": 0.28, "EHPAD": 0.12, "deces": 0.10, "REA": 0.01, "USC": 0.02, "transfert": 0.02, "MED_INT": 0.01}
    if service == "ONCO":
        return {"domicile": 0.74, "deces": 0.09, "SSR": 0.06, "USC": 0.03, "REA": 0.02, "MED_INT": 0.03, "transfert": 0.03}
    if service in CHIRURGIES:
        ssr = 0.30 if (age80 and service == "ORTHO") else (0.12 if age80 else (0.06 if age65 else 0.02))
        d = {"domicile": 1.0, "SSR": ssr, "REA": 0.04, "USC": 0.05, "deces": 0.03 if age80 else 0.006,
             "transfert": 0.01, "MED_INT": 0.02 if not enfant else 0.0, "EHPAD": 0.03 if age80 else 0.0}
        d["domicile"] = max(0.05, 1.0 - sum(v for k, v in d.items() if k != "domicile"))
        return d
    # médecines (CARDIO, PNEUMO, MED_INT, NEURO, GASTRO, MED_POLY)
    d = {"REA": 0.045, "USC": 0.035, "transfert": 0.02}
    if service == "CARDIO":
        d["USIC"] = 0.12 if mode_entree == "urgences" else 0.04
    if service == "NEURO":
        d["SSR"] = 0.30 if age80 else (0.20 if age65 else 0.10)
    else:
        d["SSR"] = 0.22 if age80 else (0.12 if age65 else 0.04)
    d["EHPAD"] = 0.08 if age80 else 0.0
    d["deces"] = 0.08 if age80 else (0.04 if age65 else 0.012)
    if age80 and service in {"MED_POLY", "MED_INT", "PNEUMO", "CARDIO"}:
        d["GERIA"] = 0.04
    if mode_entree == "programme":
        for k in ("REA", "USC", "SSR", "deces", "EHPAD"):
            d[k] = d.get(k, 0.0) * 0.5
    d["domicile"] = max(0.05, 1.0 - sum(d.values()))
    # on évite de renvoyer vers un service de médecine déjà visité (hors soins critiques)
    for k in list(d):
        if k in visites and k in MEDECINES | CHIRURGIES:
            d.pop(k)
    return d


def tirer(rng: np.random.Generator, d: dict) -> object:
    cles = list(d)
    p = np.array([d[k] for k in cles], dtype=float)
    return cles[int(rng.choice(len(cles), p=p / p.sum()))]


def tirer_duree_j(rng: np.random.Generator, service: str, mode_entree: str, classe_age: str) -> float:
    if rng.random() < PART_COURTE.get(service, 0.06):
        return float(rng.uniform(2, 20)) / 24.0
    med = MEDIANE_DUREE[service][0 if mode_entree == "urgences" else 1] * FACTEUR_AGE[classe_age]
    return max(1.0, float(rng.lognormal(np.log(med), SIGMA_DUREE)))


def generer_sejour(rng: np.random.Generator, id_sejour: int, premier: str, mode_entree: str, classe_age: str, sexe: str) -> list[dict]:
    """Génère les étapes d'un séjour fictif (sans contrainte de lits)."""
    etapes, service, origine, visites = [], premier, None, set()
    mode_sortie = "domicile"
    for rang in range(1, 9):
        visites.add(service)
        duree = tirer_duree_j(rng, service, mode_entree, classe_age)
        etapes.append({"id_sejour": id_sejour, "rang": rang, "specialite": service, "specialite_prevue": service,
                       "heberge": False, "duree_j": duree, "mode_entree": mode_entree, "classe_age": classe_age, "sexe": sexe})
        if rang == 8:
            break
        suite = tirer(rng, suite_reelle(service, origine, classe_age, mode_entree, visites))
        if suite in SORTIES:
            mode_sortie = suite
            break
        if service in MEDECINES | CHIRURGIES | {"PED", "GYN_OBS"}:
            origine = service
        service = suite
    for e in etapes:
        e["mode_sortie"] = mode_sortie
    return etapes


# ---------------------------------------------------------------------------
# 6. Génération d'une population de séjours et calibration
# ---------------------------------------------------------------------------
def generer_population(rng: np.random.Generator, annees: float, entrees_prog: dict[str, float]) -> pd.DataFrame:
    etapes, id_sejour = [], 0
    # Urgences
    n_pass = int(PASSAGES_URGENCES_PAR_AN * annees)
    cles_as = list(PROFIL_AGE_SEXE_URGENCES)
    p_as = np.array([PROFIL_AGE_SEXE_URGENCES[k] for k in cles_as])
    idx = rng.choice(len(cles_as), size=n_pass, p=p_as / p_as.sum())
    for i in idx:
        classe_age, sexe = cles_as[i]
        gravite = tirer(rng, GRAVITE_PAR_AGE[classe_age])
        if tirer(rng, issue_urgences(classe_age, gravite)) != "hospitalisation":
            continue
        premier = tirer(rng, premier_service_urgences(classe_age, sexe))
        id_sejour += 1
        etapes += generer_sejour(rng, id_sejour, premier, "urgences", classe_age, sexe)
    # Programmé
    for service, par_semaine in entrees_prog.items():
        n = int(round(par_semaine * 52.18 * annees))
        prof = profil_patients_programme(service)
        cles = list(prof)
        p = np.array([prof[k] for k in cles])
        for i in rng.choice(len(cles), size=n, p=p / p.sum()):
            classe_age, sexe = cles[i]
            id_sejour += 1
            etapes += generer_sejour(rng, id_sejour, service, "programme", classe_age, sexe)
    return pd.DataFrame(etapes)


def occupation_par_service(etapes: pd.DataFrame, annees: float) -> pd.Series:
    journees = etapes.groupby("specialite")["duree_j"].sum() / annees
    lits = pd.Series({s: v[2] for s, v in SERVICES.items() if v[2] > 0})
    return (journees / (lits * 365.0)).reindex(lits.index).fillna(0.0)


def calibrer(rng: np.random.Generator, annees: float) -> tuple[dict[str, float], pd.DataFrame]:
    """Ajuste les entrées programmées pour approcher les taux d'occupation visés."""
    entrees = dict(ENTREES_PROGRAMMEES_SEMAINE)
    for iteration in range(6):
        pop = generer_population(rng, annees, entrees)
        occ = occupation_par_service(pop, annees)
        for s in entrees:
            ratio = OCCUPATION_CIBLE[s] / max(occ[s], 1e-6)
            entrees[s] = max(1.0, entrees[s] * float(np.clip(ratio, 0.6, 1.6)))
        ecart = max(abs(occ[s] - OCCUPATION_CIBLE[s]) for s in entrees)
        print(f"  itération {iteration + 1} : écart max sur les services programmés = {ecart:.3f}")
        if ecart < 0.015:
            break
    return {s: round(v, 1) for s, v in entrees.items()}, pop


# ---------------------------------------------------------------------------
# 7. Écriture des fichiers
# ---------------------------------------------------------------------------
def ecrire(dossier: Path, graine: int, seuil: int, annees: float) -> None:
    dossier.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(graine)

    print("Calibration des entrées programmées…")
    entrees_prog, pop = calibrer(rng, annees)
    occ = occupation_par_service(pop, annees)

    print("Extraction des paramètres…")
    fusion = extraction.fusionner_etapes(pop)
    traj = extraction.trajectoires(fusion)
    cat = extraction.catalogue(traj, seuil)
    prem = extraction.premier_service(traj, seuil)
    # La table de passage décrit les séjours que le catalogue ne couvre pas :
    # c'est elle qui prend le relais pour ces séjours dans le simulateur.
    reste = extraction.hors_catalogue(traj, cat)
    tp = extraction.table_passage(reste, seuil)
    dur = extraction.durees(fusion, seuil)

    cat.to_csv(dossier / "catalogue_trajectoires.csv", index=False)
    prem.to_csv(dossier / "premier_service.csv", index=False, float_format="%.5f")
    tp.to_csv(dossier / "table_passage.csv", index=False, float_format="%.5f")
    dur.to_csv(dossier / "durees_sejour.csv", index=False)

    # Profils des urgences
    lignes = [("heure", str(h), w) for h, w in PROFIL_HEURE_URGENCES.items()]
    lignes += [("jour_semaine", j, w) for j, w in PROFIL_JOUR_URGENCES.items()]
    lignes += [("mois", str(m), w) for m, w in PROFIL_MOIS_URGENCES.items()]
    pd.DataFrame(lignes, columns=["dimension", "valeur", "poids"]).to_csv(dossier / "profil_arrivees_urgences.csv", index=False)
    pd.DataFrame([(a, s, p) for (a, s), p in PROFIL_AGE_SEXE_URGENCES.items()],
                 columns=["classe_age", "sexe", "probabilite"]).to_csv(dossier / "profil_patients_urgences.csv", index=False)
    pd.DataFrame([(a, g, p) for a, d in GRAVITE_PAR_AGE.items() for g, p in d.items()],
                 columns=["classe_age", "gravite", "probabilite"]).to_csv(dossier / "gravite_urgences.csv", index=False)
    pd.DataFrame([(a, g, i, round(p, 4)) for a in CLASSES_AGE for g in range(1, 6) for i, p in issue_urgences(a, g).items()],
                 columns=["classe_age", "gravite", "issue", "probabilite"]).to_csv(dossier / "issues_urgences.csv", index=False)
    pd.DataFrame([(i, *v) for i, v in DUREE_PASSAGE_URGENCES.items()],
                 columns=["issue", "mediane_h", "q1_h", "q3_h"]).to_csv(dossier / "durees_passage_urgences.csv", index=False)

    # Entrées programmées
    pd.DataFrame([(s, v) for s, v in entrees_prog.items()], columns=["service", "entrees_par_semaine"]).to_csv(
        dossier / "entrees_programmees.csv", index=False)
    lignes = [("jour_semaine", j, w) for j, w in PROFIL_JOUR_PROGRAMME.items()]
    lignes += [("heure", str(h), w) for h, w in PROFIL_HEURE_PROGRAMME.items()]
    pd.DataFrame(lignes, columns=["dimension", "valeur", "poids"]).to_csv(dossier / "profil_entrees_programmees.csv", index=False)
    pd.DataFrame([(s, a, sx, round(p, 4)) for s in entrees_prog for (a, sx), p in profil_patients_programme(s).items()],
                 columns=["service", "classe_age", "sexe", "probabilite"]).to_csv(dossier / "profil_patients_programme.csv", index=False)

    # Sorties : profil horaire
    profil_sorties = {h: w for h, w in zip(range(24), [0.2, 0.1, 0.1, 0.1, 0.1, 0.2, 0.5, 1, 2, 3.5, 5, 7, 7.5, 6.5, 6, 5.5, 5, 4, 3, 2, 1.2, 0.8, 0.5, 0.3])}
    pd.DataFrame(list(profil_sorties.items()), columns=["heure", "poids"]).to_csv(dossier / "profil_sorties.csv", index=False)

    # Cibles de contrôle
    # Tolérance plus large pour les petits services, dont l'occupation annuelle
    # varie davantage d'une simulation à l'autre (et le simulateur ajoute
    # attentes et hébergements, qui déplacent un peu les journées-lits).
    def tol(s: str) -> float:
        return 0.05 + 0.3 / SERVICES[s][2]
    cibles = [("taux_occupation", s, round(max(0.0, occ[s] - tol(s)), 3), round(min(1.0, occ[s] + tol(s) + 0.02), 3)) for s in occ.index]
    dms = fusion.groupby("specialite")["duree_j"].mean()
    cibles += [("dms_j", s, round(v * 0.85, 2), round(v * 1.15, 2)) for s, v in dms.items()]
    part_urg = (traj["mode_entree"] == "urgences").mean()
    cibles += [("part_sejours_urgences", "", round(part_urg - 0.05, 3), round(part_urg + 0.05, 3))]
    n_hosp = (traj["mode_entree"] == "urgences").sum() / annees
    taux_hosp = n_hosp / PASSAGES_URGENCES_PAR_AN
    cibles += [("taux_hospitalisation_urgences", "", round(taux_hosp - 0.03, 3), round(taux_hosp + 0.03, 3))]
    nb_et = traj["nb_etapes"].mean()
    cibles += [("nb_rum_moyen", "", round(nb_et * 0.9, 2), round(nb_et * 1.15, 2))]
    cibles += [("taux_hebergement", "", 0.0, 0.12), ("attente_mediane_urgences_h", "", 0.0, 6.0), ("taux_transfert_faute_de_lit", "", 0.0, 0.03)]
    pd.DataFrame(cibles, columns=["indicateur", "service", "valeur_min", "valeur_max"]).to_csv(dossier / "cibles.csv", index=False)

    # Structure et règles (YAML)
    structure = {
        "etablissement": "HOPITAL_FICTIF_480",
        "description": "Hôpital fictif d'environ 480 lits, paramètres fabriqués par scripts/fabriquer_parametres.py",
        "debut": "2026-01-01",
        "unite_de_temps": "heure",
        "classes_age": CLASSES_AGE,
        "services": {
            code: {"libelle": lib, "type": typ, "lits": lits, "hebergement": heb, **contr}
            for code, (lib, typ, lits, heb, contr) in SERVICES.items()
        },
        "urgences": {
            "passages_par_an": PASSAGES_URGENCES_PAR_AN,
            "delai_max_attente_h": 6,
            "delai_transfert_h": 24,
            "pas_reessai_h": 1,
        },
        "regles": {
            "delai_max_attente_mutation_h": 12,
            "retour_service_origine": True,
            "duree_min_restante_pour_retour_h": 12,
            "priorite_par_gravite": {1: 2, 2: 2, 3: 1, 4: 0, 5: 0},
            "priorite_programme": 2,
            "priorite_mutation": 1,
        },
        "sorties": {
            "facteur_weekend": 0.45,
            "services_sans_effet_weekend": ["UHCD", "REA", "USC", "USIC"],
        },
        "population": {"probabilite_retour": {"0-17": 0.10, "18-64": 0.12, "65-79": 0.20, "80+": 0.28}},
        "simulation": {"duree_jours": 365, "chauffe_jours": 42, "marge_fin_jours": 60, "graine": 42},
        "confidentialite": {"seuil_effectif": seuil},
    }
    with open(dossier / "structure.yaml", "w", encoding="utf-8") as f:
        yaml.safe_dump(structure, f, allow_unicode=True, sort_keys=False)

    print("\nOccupation attendue (sans contrainte de lits) :")
    for s, v in occ.items():
        print(f"  {s:9s} {v:5.2f}  (cible {OCCUPATION_CIBLE[s]:.2f})")
    print(f"\nSéjours fictifs générés : {traj.shape[0]} sur {annees:.0f} ans ; part venant des urgences : {part_urg:.2f} ; "
          f"taux d'hospitalisation après urgences : {taux_hosp:.3f} ; étapes par séjour : {nb_et:.2f}")
    couverture = cat["effectif"].sum() / len(traj)
    print(f"Catalogue : {len(cat)} trajectoires au-dessus du seuil {seuil}, couvrant {couverture:.1%} des séjours")
    print(f"Fichiers écrits dans {dossier}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dossier", type=Path)
    ap.add_argument("--graine", type=int, default=7)
    ap.add_argument("--seuil", type=int, default=20)
    ap.add_argument("--annees", type=float, default=3.0, help="années de séjours fictifs générés pour l'extraction")
    args = ap.parse_args()
    ecrire(args.dossier, args.graine, args.seuil, args.annees)
