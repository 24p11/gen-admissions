"""Fabrique les 22 fichiers de paramètres (format v2) d'un hôpital fictif.

Principe : on invente d'abord une « réalité » fictive — des venues aux urgences,
des séjours avec leurs RUM, des mouvements, sur trois années et deux sites —
puis on lui applique exactement les fonctions d'extraction du module
``gen_admissions.preparation_parametres``, comme le fait le notebook sur les
données réelles. Les fichiers produits sont donc conformes au dictionnaire par
construction, et le script sert de banc d'essai permanent au simulateur comme à
l'extraction.

Le site simulé est ``HOPITAL_FICTIF_480`` (site A, environ 480 lits). Un second
site (B), avec une structure différente, n'existe que pour fabriquer les deux
tables de pratiques multi-sites (``premier_service_racine``, ``table_passage``)
avec des services absents du site A, afin d'exercer la restriction par la
structure et le transfert.

Usage :  python scripts/fabriquer_parametres_fictifs.py [--dossier parametres/hopital_type] [--graine 42]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gen_admissions.preparation_parametres import (  # noqa: E402
    REGLAGES, additionner, cible, classe_age, classer_tension, classes_tension_depuis_tension, configurer,
    durees_passage_depuis_passages, durees_urgences_depuis_effectifs, effectifs_arrivees, effectifs_durees_urgences,
    effectifs_gravite, effectifs_passage, effectifs_patients_urgences, effectifs_profil_semaine,
    effectifs_supplement_rang2, effectifs_venues, etapes_sejours, gravite_depuis_effectifs, normaliser,
    passages_specialite, patients_urgences_depuis_effectifs, premier_service_depuis_effectifs,
    profil_depuis_effectifs, saison, saisons_completes, services_vers_yaml, soins_critiques_depuis_effectifs,
    supplement_depuis_effectifs, table_passage_depuis_effectifs, table_proportions, tension_journaliere,
    venues_depuis_effectifs, verifier_dossier, COLONNES_PROFIL_ARRIVEES,
)

ANNEES = [2023, 2024, 2025]
ANNEE_RECENTE = 2025
SEUIL, SEUIL_LISSAGE, PART_MIN = 25, 20, 0.10
CLASSES_AGE = ["0-17", "18-64", "65-74", "75+"]
SITE_A, SITE_B = "A", "B"
NB_JOURS = 365

# ---------------------------------------------------------------------------
# 1. L'hôpital fictif : services, racines de GHM, lois
# ---------------------------------------------------------------------------

# code : (libellé, type, lits, soins critiques couverts, age_min, age_max, sexe)
SERVICES = {
    "URG":      ("Urgences (dont UHCD)", "urgences", 10, None, None, None, None),
    "REA":      ("Réanimation et surveillance continue", "soins_critiques", 32, ["rea", "usc"], 18, None, None),
    "USIC":     ("Soins intensifs cardiologiques", "soins_critiques", 8, ["usi"], 18, None, None),
    "UNV":      ("Unité neuro-vasculaire", "soins_critiques", 6, ["usi"], 18, None, None),
    "CARDIO":   ("Cardiologie", "medecine", 36, None, 18, None, None),
    "PNEUMO":   ("Pneumologie", "medecine", 30, None, 18, None, None),
    "MED_INT":  ("Médecine interne", "medecine", 40, None, 18, None, None),
    "GERIA":    ("Gériatrie aiguë", "medecine", 40, None, 75, None, None),
    "NEURO":    ("Neurologie", "medecine", 30, None, 18, None, None),
    "GASTRO":   ("Gastro-entérologie", "medecine", 16, None, 18, None, None),
    "ONCO":     ("Oncologie-hématologie", "medecine", 30, None, 18, None, None),
    "MED_POLY": ("Médecine polyvalente post-urgences", "medecine", 40, None, 18, None, None),
    "ORTHO":    ("Orthopédie-traumatologie", "chirurgie", 36, None, 18, None, None),
    "CHIR_DIG": ("Chirurgie digestive", "chirurgie", 32, None, 18, None, None),
    "URO":      ("Urologie", "chirurgie", 22, None, 18, None, None),
    "GYN_OBS":  ("Gynécologie-obstétrique", "obstetrique", 34, None, None, None, "F"),
    "PED":      ("Pédiatrie", "pediatrie", 16, None, None, 17, None),
    # présents sur le site B seulement (tables multi-sites)
    "NEUROCHIR": ("Neurochirurgie", "chirurgie", 30, None, 18, None, None),
    "CHIR_CARD": ("Chirurgie cardiaque", "chirurgie", 24, None, 18, None, None),
}
SERVICES_SITE = {
    SITE_A: [s for s in SERVICES if s not in ("NEUROCHIR", "CHIR_CARD")],
    SITE_B: [s for s in SERVICES if s not in ("ONCO", "GERIA", "UNV")],
}
SOINS_CRITIQUES = {"rea": "REA", "usc": "REA", "usi_cardio": "USIC", "usi_neuro": "UNV"}

# racine : libellé, da, poids casemix, part venue des urgences, âges (0-17, 18-64, 65-74, 75+), part F,
#          services {code: poids}, DMS médiane (j), parts soins critiques (rea, usi, usc), sorties (dom, SSR, EHPAD, transfert, décès)
RACINES = {
    "05M09": ("Insuffisances cardiaques et états de choc circulatoire", "D01", 4.0, 0.85, (0, 10, 25, 65), 0.50,
              {"CARDIO": 0.55, "GERIA": 0.25, "MED_INT": 0.10, "MED_POLY": 0.10}, 7.5, (0.03, 0.25, 0.05), (0.62, 0.20, 0.06, 0.04, 0.08)),
    "05M04": ("Infarctus aigu du myocarde", "D01", 2.0, 0.80, (0, 45, 30, 25), 0.35,
              {"CARDIO": 0.90, "MED_INT": 0.10}, 5.0, (0.05, 0.85, 0.05), (0.80, 0.08, 0.01, 0.06, 0.05)),
    "05M06": ("Arythmies et troubles de la conduction", "D01", 2.5, 0.60, (0, 40, 30, 30), 0.45,
              {"CARDIO": 0.85, "MED_INT": 0.08, "GERIA": 0.07}, 3.5, (0.01, 0.30, 0.03), (0.90, 0.04, 0.01, 0.03, 0.02)),
    "05M13": ("Douleurs thoraciques", "D01", 2.0, 0.90, (0, 60, 25, 15), 0.45,
              {"CARDIO": 0.70, "MED_POLY": 0.20, "MED_INT": 0.10}, 1.8, (0.0, 0.10, 0.01), (0.97, 0.01, 0.0, 0.01, 0.01)),
    "04M05": ("Pneumonies et pleurésies banales", "D02", 3.5, 0.85, (10, 25, 20, 45), 0.45,
              {"PNEUMO": 0.40, "MED_INT": 0.20, "GERIA": 0.20, "MED_POLY": 0.15, "PED": 0.05}, 7.0, (0.04, 0.0, 0.08), (0.70, 0.14, 0.05, 0.03, 0.08)),
    "04M20": ("Bronchopneumopathies chroniques surinfectées", "D02", 2.0, 0.85, (0, 20, 35, 45), 0.40,
              {"PNEUMO": 0.65, "MED_INT": 0.15, "GERIA": 0.10, "MED_POLY": 0.10}, 7.5, (0.03, 0.0, 0.10), (0.75, 0.12, 0.03, 0.03, 0.07)),
    "04M13": ("Œdème pulmonaire et détresse respiratoire", "D02", 1.0, 0.90, (0, 25, 30, 45), 0.45,
              {"PNEUMO": 0.40, "REA": 0.0, "MED_INT": 0.25, "GERIA": 0.20, "MED_POLY": 0.15}, 8.0, (0.20, 0.0, 0.20), (0.60, 0.15, 0.05, 0.05, 0.15)),
    "01M30": ("Accidents vasculaires intracérébraux non transitoires", "D03", 2.5, 0.90, (0, 30, 30, 40), 0.48,
              {"NEURO": 0.70, "GERIA": 0.15, "MED_INT": 0.10, "MED_POLY": 0.05}, 9.0, (0.05, 0.60, 0.05), (0.40, 0.38, 0.05, 0.07, 0.10)),
    "01M31": ("Accidents ischémiques transitoires", "D03", 1.2, 0.85, (0, 35, 30, 35), 0.48,
              {"NEURO": 0.80, "GERIA": 0.10, "MED_INT": 0.10}, 3.0, (0.0, 0.50, 0.0), (0.92, 0.04, 0.01, 0.02, 0.01)),
    "01M16": ("Épilepsie", "D03", 1.2, 0.80, (20, 55, 12, 13), 0.45,
              {"NEURO": 0.70, "PED": 0.20, "MED_POLY": 0.10}, 3.5, (0.03, 0.05, 0.03), (0.94, 0.02, 0.01, 0.02, 0.01)),
    "01C13": ("Craniotomies", "D03", 0.6, 0.40, (0, 55, 30, 15), 0.45,
              {"NEUROCHIR": 0.95, "NEURO": 0.05}, 10.0, (0.60, 0.0, 0.30), (0.60, 0.25, 0.01, 0.08, 0.06)),
    "01C04": ("Interventions sur le rachis", "D03", 0.8, 0.15, (0, 60, 30, 10), 0.45,
              {"NEUROCHIR": 1.0}, 6.0, (0.05, 0.0, 0.25), (0.85, 0.10, 0.0, 0.03, 0.02)),
    "06M03": ("Hémorragies digestives", "D04", 1.5, 0.85, (0, 35, 25, 40), 0.40,
              {"GASTRO": 0.55, "MED_INT": 0.20, "GERIA": 0.15, "MED_POLY": 0.10}, 5.5, (0.06, 0.0, 0.10), (0.80, 0.08, 0.03, 0.03, 0.06)),
    "06M05": ("Occlusions intestinales non dues à une hernie", "D04", 1.0, 0.80, (5, 40, 25, 30), 0.50,
              {"CHIR_DIG": 0.55, "GASTRO": 0.30, "PED": 0.05, "MED_POLY": 0.10}, 6.0, (0.03, 0.0, 0.05), (0.84, 0.06, 0.03, 0.03, 0.04)),
    "06C04": ("Interventions majeures sur l'intestin grêle et le côlon", "D04", 1.5, 0.30, (0, 45, 30, 25), 0.48,
              {"CHIR_DIG": 1.0}, 9.0, (0.10, 0.0, 0.35), (0.80, 0.10, 0.01, 0.04, 0.05)),
    "06C09": ("Appendicectomies", "D04", 1.5, 0.95, (35, 60, 3, 2), 0.45,
              {"CHIR_DIG": 0.80, "PED": 0.20}, 2.5, (0.0, 0.0, 0.02), (0.99, 0.0, 0.0, 0.01, 0.0)),
    "07M06": ("Affections des voies biliaires", "D04", 1.0, 0.80, (0, 45, 25, 30), 0.55,
              {"GASTRO": 0.50, "CHIR_DIG": 0.35, "MED_POLY": 0.15}, 4.5, (0.02, 0.0, 0.05), (0.92, 0.03, 0.01, 0.02, 0.02)),
    "07C14": ("Cholécystectomies sans exploration de la voie biliaire", "D04", 1.2, 0.35, (0, 60, 25, 15), 0.60,
              {"CHIR_DIG": 1.0}, 2.5, (0.0, 0.0, 0.03), (0.98, 0.01, 0.0, 0.01, 0.0)),
    "08C24": ("Prothèses de hanche pour des affections autres que des traumatismes", "D05", 1.5, 0.05, (0, 25, 40, 35), 0.55,
              {"ORTHO": 1.0}, 5.5, (0.0, 0.0, 0.05), (0.70, 0.28, 0.01, 0.01, 0.0)),
    "08C27": ("Autres interventions sur la hanche et le fémur", "D05", 1.5, 0.90, (0, 20, 20, 60), 0.70,
              {"ORTHO": 0.95, "GERIA": 0.05}, 10.0, (0.02, 0.0, 0.08), (0.35, 0.50, 0.08, 0.02, 0.05)),
    "08C32": ("Interventions sur la jambe, âge supérieur à 17 ans", "D05", 1.5, 0.60, (0, 70, 20, 10), 0.45,
              {"ORTHO": 1.0}, 4.0, (0.0, 0.0, 0.03), (0.92, 0.06, 0.0, 0.02, 0.0)),
    "08C38": ("Interventions sur le membre supérieur", "D05", 1.2, 0.55, (15, 60, 15, 10), 0.45,
              {"ORTHO": 0.90, "PED": 0.10}, 1.5, (0.0, 0.0, 0.01), (0.98, 0.01, 0.0, 0.01, 0.0)),
    "08M31": ("Fractures du bassin et de la hanche, traitement médical", "D05", 0.8, 0.90, (0, 10, 20, 70), 0.70,
              {"GERIA": 0.50, "ORTHO": 0.30, "MED_POLY": 0.20}, 9.0, (0.0, 0.0, 0.02), (0.30, 0.50, 0.12, 0.03, 0.05)),
    "11C05": ("Interventions transurétrales", "D06", 1.5, 0.15, (0, 30, 40, 30), 0.15,
              {"URO": 1.0}, 2.5, (0.0, 0.0, 0.02), (0.97, 0.01, 0.01, 0.01, 0.0)),
    "11M04": ("Infections des reins et des voies urinaires", "D06", 2.0, 0.85, (10, 30, 20, 40), 0.65,
              {"URO": 0.20, "MED_INT": 0.25, "GERIA": 0.25, "MED_POLY": 0.20, "PED": 0.10}, 5.0, (0.03, 0.0, 0.03), (0.86, 0.06, 0.04, 0.02, 0.02)),
    "11M07": ("Lithiases urinaires", "D06", 1.0, 0.90, (0, 75, 15, 10), 0.35,
              {"URO": 0.85, "MED_POLY": 0.15}, 1.5, (0.0, 0.0, 0.01), (0.99, 0.0, 0.0, 0.01, 0.0)),
    "14Z14": ("Accouchements uniques par voie basse", "D15", 4.0, 0.0, (0, 100, 0, 0), 1.0,
              {"GYN_OBS": 1.0}, 3.5, (0.0, 0.0, 0.01), (1.0, 0.0, 0.0, 0.0, 0.0)),
    "14C08": ("Césariennes", "D15", 1.5, 0.0, (0, 100, 0, 0), 1.0,
              {"GYN_OBS": 1.0}, 5.0, (0.01, 0.0, 0.05), (0.99, 0.0, 0.0, 0.01, 0.0)),
    "13C03": ("Hystérectomies", "D15", 0.8, 0.05, (0, 60, 30, 10), 1.0,
              {"GYN_OBS": 1.0}, 3.5, (0.0, 0.0, 0.03), (0.98, 0.01, 0.0, 0.01, 0.0)),
    "17M06": ("Chimiothérapie pour autre tumeur", "D13", 2.5, 0.10, (0, 50, 30, 20), 0.50,
              {"ONCO": 0.90, "MED_INT": 0.10}, 4.0, (0.01, 0.0, 0.02), (0.90, 0.03, 0.01, 0.02, 0.04)),
    "17M05": ("Tumeurs malignes hématologiques", "D13", 1.0, 0.40, (5, 45, 30, 20), 0.45,
              {"ONCO": 0.80, "MED_INT": 0.15, "PED": 0.05}, 12.0, (0.06, 0.0, 0.06), (0.80, 0.05, 0.01, 0.04, 0.10)),
    "23M20": ("Autres symptômes et motifs de recours aux soins", "D27", 3.0, 0.90, (10, 30, 20, 40), 0.52,
              {"MED_POLY": 0.40, "MED_INT": 0.25, "GERIA": 0.25, "PED": 0.10}, 4.0, (0.01, 0.0, 0.02), (0.85, 0.07, 0.04, 0.02, 0.02)),
    "18M07": ("Septicémies", "D27", 1.2, 0.85, (0, 25, 30, 45), 0.45,
              {"MED_INT": 0.40, "GERIA": 0.25, "MED_POLY": 0.20, "PNEUMO": 0.15}, 9.0, (0.20, 0.0, 0.15), (0.55, 0.15, 0.08, 0.04, 0.18)),
    "10M06": ("Diabète", "D08", 1.2, 0.60, (10, 55, 20, 15), 0.45,
              {"MED_INT": 0.70, "PED": 0.10, "MED_POLY": 0.20}, 5.0, (0.01, 0.0, 0.02), (0.95, 0.02, 0.01, 0.01, 0.01)),
    "16M11": ("Anémies", "D11", 1.0, 0.70, (5, 25, 25, 45), 0.55,
              {"MED_INT": 0.45, "GERIA": 0.30, "ONCO": 0.15, "MED_POLY": 0.10}, 4.0, (0.0, 0.0, 0.02), (0.88, 0.06, 0.03, 0.02, 0.01)),
    "04M04": ("Bronchiolites", "D02", 1.0, 0.95, (100, 0, 0, 0), 0.45,
              {"PED": 1.0}, 3.0, (0.03, 0.0, 0.05), (0.98, 0.0, 0.0, 0.02, 0.0)),
    "06M08": ("Gastro-entérites et maladies diverses du tube digestif", "D04", 1.5, 0.90, (50, 20, 10, 20), 0.48,
              {"PED": 0.50, "MED_POLY": 0.25, "GASTRO": 0.15, "GERIA": 0.10}, 2.5, (0.0, 0.0, 0.01), (0.97, 0.01, 0.01, 0.01, 0.0)),
    "05C05": ("Chirurgie cardiaque", "D01", 0.6, 0.10, (0, 45, 35, 20), 0.30,
              {"CHIR_CARD": 1.0}, 11.0, (0.95, 0.10, 0.60), (0.70, 0.20, 0.0, 0.05, 0.05)),
}
NIVEAUX = ["1", "2", "3", "4"]
NIVEAUX_POIDS_BASE = np.array([0.50, 0.28, 0.16, 0.06])
NIVEAUX_POIDS_AGE = {"0-17": np.array([0.70, 0.20, 0.08, 0.02]), "18-64": np.array([0.58, 0.26, 0.12, 0.04]),
                     "65-74": np.array([0.42, 0.30, 0.20, 0.08]), "75+": np.array([0.30, 0.32, 0.26, 0.12])}
FACTEUR_DUREE_NIVEAU = {"1": 0.70, "2": 1.0, "3": 1.45, "4": 2.1}
FACTEUR_DUREE_AGE = {"0-17": 0.75, "18-64": 0.85, "65-74": 1.0, "75+": 1.25}

# Urgences
PASSAGES_PAR_AN = {SITE_A: 45_000, SITE_B: 38_000}
PROFIL_HEURE = np.array([0.6, 0.45, 0.35, 0.3, 0.3, 0.35, 0.5, 0.8, 1.2, 1.6, 1.8, 1.75, 1.5, 1.45, 1.5, 1.5, 1.45, 1.4, 1.35, 1.3, 1.2, 1.05, 0.9, 0.75])
PROFIL_JOUR = np.array([1.12, 1.02, 0.99, 0.98, 1.00, 0.94, 0.95])          # lundi … dimanche
PROFIL_MOIS = {2023: [1.06, 1.02, 1.03, 0.99, 1.00, 1.00, 0.97, 0.94, 0.98, 0.99, 0.98, 1.04],
               2024: [1.10, 1.06, 1.02, 0.99, 1.00, 1.00, 0.97, 0.94, 0.98, 0.99, 1.00, 1.12],
               2025: [1.16, 1.08, 1.01, 0.99, 1.00, 1.00, 0.97, 0.94, 0.98, 0.99, 0.99, 1.06]}
AGES_URGENCES = np.array([0.22, 0.50, 0.11, 0.17])
PART_F_URGENCES = {"0-17": 0.46, "18-64": 0.49, "65-74": 0.50, "75+": 0.58}
# situation sachant la classe d'âge : consultation, mono_uhcd, hospitalisation, transfert, deces
SITUATIONS = ["consultation", "mono_uhcd", "hospitalisation", "transfert", "deces"]
SITUATION_PAR_AGE = {"0-17": [0.93, 0.03, 0.03, 0.01, 0.0], "18-64": [0.86, 0.06, 0.05, 0.03, 0.0],
                     "65-74": [0.72, 0.10, 0.14, 0.035, 0.005], "75+": [0.55, 0.14, 0.26, 0.04, 0.01]}
CCMU_PAR_SITUATION = {"consultation": [0.10, 0.55, 0.30, 0.04, 0.01], "mono_uhcd": [0.0, 0.25, 0.60, 0.13, 0.02],
                      "hospitalisation": [0.0, 0.15, 0.60, 0.20, 0.05], "transfert": [0.0, 0.20, 0.50, 0.22, 0.08],
                      "deces": [0.0, 0.0, 0.10, 0.30, 0.60]}
DUREE_MEDIANE_H = {"consultation": 3.0, "mono_uhcd": 16.0, "hospitalisation": 7.0, "transfert": 5.0, "deces": 6.0}
FACTEUR_CCMU = {"1": 0.7, "2": 0.9, "3": 1.1, "4": 1.3, "5": 1.2}
FACTEUR_TENSION_HOSP = {"basse": 0.85, "moyenne": 1.0, "haute": 1.30}
PART_UHCD_AVANT_SERVICE = 0.35          # part des hospitalisés qui passent par un lit d'UHCD avant le service
PROFIL_HEURE_PROGRAMME = np.array([0, 0, 0, 0, 0, 0, 0.5, 4, 6, 4, 2, 1.5, 1, 1.5, 2, 2, 2.5, 3, 2, 1, 0, 0, 0, 0])
PROFIL_JOUR_PROGRAMME = np.array([1.7, 1.3, 1.1, 1.1, 0.5, 0.1, 0.4])
PROFIL_HEURE_SORTIE = np.array([0.1, 0.05, 0.05, 0.05, 0.05, 0.05, 0.1, 0.3, 0.6, 1.2, 2.0, 2.6, 2.2, 1.8, 1.9, 1.8, 1.6, 1.2, 0.8, 0.5, 0.3, 0.2, 0.15, 0.1])
PROFIL_JOUR_SORTIE = np.array([1.05, 1.1, 1.1, 1.1, 1.3, 0.75, 0.6])


def choisir(rng: np.random.Generator, options, poids, n: int) -> np.ndarray:
    p = np.asarray(poids, dtype=float)
    return rng.choice(np.asarray(options, dtype=object), size=n, p=p / p.sum())


def heures_profil(rng: np.random.Generator, n: int, poids_heure: np.ndarray) -> np.ndarray:
    """Heure décimale dans la journée, tirée sur un profil horaire."""
    h = rng.choice(24, size=n, p=poids_heure / poids_heure.sum())
    return h + rng.random(n)


def dates_profil(rng: np.random.Generator, n: int, annee: int, poids_jour: np.ndarray, poids_mois: np.ndarray | None,
                 poids_heure: np.ndarray) -> pd.Series:
    """Dates et heures dans l'année, tirées sur des profils jour de semaine × mois × heure."""
    jours = pd.date_range(f"{annee}-01-01", f"{annee}-12-31", freq="D")
    poids = poids_jour[jours.dayofweek]
    if poids_mois is not None:
        poids = poids * np.asarray(poids_mois)[jours.month - 1]
    j = rng.choice(len(jours), size=n, p=poids / poids.sum())
    return pd.Series(jours[j]) + pd.to_timedelta(heures_profil(rng, n, poids_heure), unit="h")


# ---------------------------------------------------------------------------
# 2. La réalité fictive : venues, séjours, RUM
# ---------------------------------------------------------------------------

def services_du_site(site: str, services: dict[str, float]) -> dict[str, float]:
    """La pratique du site : les services de la racine présents sur le site (l'hôpital envoie
    ailleurs ce qu'il ne peut pas prendre, mais ces séjours n'existent pas chez lui)."""
    presents = {s: p for s, p in services.items() if s in SERVICES_SITE[site] and p > 0}
    return presents


def generer_sejours(rng: np.random.Generator, site: str, annee: int, venues_hosp: pd.DataFrame, n_programme: int,
                    debut_id: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Les séjours d'un site et d'une année : ceux des venues hospitalisées (dates et âges imposés
    par la venue) et ``n_programme`` entrées programmées. Renvoie (séjours, RUM)."""
    racines = list(RACINES)
    poids_casemix = np.array([RACINES[r][2] for r in racines])
    part_urg = np.array([RACINES[r][3] for r in racines])
    # racines disponibles sur le site : au moins un service présent
    dispo = np.array([len(services_du_site(site, RACINES[r][6])) > 0 for r in racines])

    # --- séjours venus des urgences : une racine sachant la classe d'âge, pondérée par la part urgences
    sej = []
    for ca, g in venues_hosp.groupby("classe_age"):
        poids_age = np.array([RACINES[r][4][CLASSES_AGE.index(ca)] for r in racines])
        p = poids_casemix * part_urg * poids_age * dispo
        sej.append(pd.DataFrame({"id_venue": g["id_venue"].values, "classe_age": ca, "sexe_venue": g["sexe"].values,
                                 "date_entree": g["date_sortie"].values, "mode_entree": "urgences",
                                 "racine_ghm": choisir(rng, racines, p, len(g))}))
    # --- entrées programmées : racine puis âge
    p = poids_casemix * (1 - part_urg) * dispo
    r_prog = choisir(rng, racines, p, n_programme)
    ages = np.array([choisir(rng, CLASSES_AGE, RACINES[r][4], 1)[0] for r in r_prog], dtype=object)
    sej.append(pd.DataFrame({"id_venue": None, "classe_age": ages, "sexe_venue": None,
                             "date_entree": dates_profil(rng, n_programme, annee, PROFIL_JOUR_PROGRAMME, None, PROFIL_HEURE_PROGRAMME).values,
                             "mode_entree": "programme", "racine_ghm": r_prog}))
    s = pd.concat(sej, ignore_index=True)
    s["nohop"] = site
    s["id_sejour"] = np.arange(debut_id, debut_id + len(s))
    n = len(s)
    attr = s["racine_ghm"].map(RACINES)
    # sexe : celui de la venue s'il existe, sinon la part de femmes de la racine
    part_f = attr.map(lambda a: a[5]).astype(float).values
    sexe = np.where(rng.random(n) < part_f, "F", "M")
    s["sexe"] = np.where(s["sexe_venue"].notna(), s["sexe_venue"], sexe)
    s.loc[s["racine_ghm"].str.startswith(("13", "14")), "sexe"] = "F"
    # niveau sachant l'âge
    s["niveau"] = ""
    for ca in CLASSES_AGE:
        m = s["classe_age"] == ca
        s.loc[m, "niveau"] = choisir(rng, NIVEAUX, NIVEAUX_POIDS_AGE[ca], int(m.sum()))
    s["ghm"] = s["racine_ghm"] + s["niveau"]
    # durée totale (jours décimaux) : log-normale autour de la médiane de la racine, corrigée par niveau et âge
    mediane = attr.map(lambda a: a[7]).astype(float).values * s["niveau"].map(FACTEUR_DUREE_NIVEAU).values \
        * s["classe_age"].map(FACTEUR_DUREE_AGE).values
    s["duree_j"] = np.maximum(0.2, np.exp(np.log(mediane) + rng.normal(0, 0.55, n)))
    # soins critiques : trois tirages indépendants, plus fréquents aux niveaux élevés
    facteur_sc = s["niveau"].map({"1": 0.5, "2": 0.9, "3": 1.4, "4": 2.2}).values
    for i, p_ in enumerate(["rea", "usi", "usc"]):
        prob = np.clip(attr.map(lambda a, i=i: a[8][i]).astype(float).values * facteur_sc, 0, 0.98)
        s[p_] = rng.random(n) < prob
    s["usc"] = s["usc"] & ~s["rea"]       # la réanimation couvre aussi la surveillance continue ici
    # mode de sortie : parts de la racine, décès et SSR plus fréquents aux niveaux élevés
    s["mode_sortie"] = ""
    for r, g in s.groupby("racine_ghm"):
        base = np.array(RACINES[r][9], dtype=float)
        for niv, gg in g.groupby("niveau"):
            p = base * np.array([1.0, 1.0, 1.0, 1.0, 1.0]) if niv in ("1", "2") else base * np.array([0.7, 1.4, 1.4, 1.2, 2.5])
            s.loc[gg.index, "mode_sortie"] = choisir(rng, ["domicile", "SSR", "EHPAD", "transfert", "deces"], p, len(gg))
    # --- trajectoire : premier service (pratique du site, les 75+ tirés vers la gériatrie), puis mutations
    s["premier_service"] = ""
    for (r, ca), g in s.groupby(["racine_ghm", "classe_age"]):
        serv = services_du_site(site, RACINES[r][6])
        # les contraintes d'âge de la structure (gériatrie à partir de 75 ans, pédiatrie jusqu'à 17)
        bas = int(ca.rstrip("+").split("-")[0]); haut = None if ca.endswith("+") else int(ca.split("-")[1])
        serv = {k: v for k, v in serv.items()
                if (SERVICES[k][4] is None or haut is None or haut >= SERVICES[k][4]) and (SERVICES[k][5] is None or bas <= SERVICES[k][5])} or serv
        if ca == "75+" and "GERIA" in SERVICES_SITE[site] and any(SERVICES[x][1] == "medecine" for x in serv):
            serv = dict(serv); serv["GERIA"] = serv.get("GERIA", 0) + 0.20 * sum(serv.values())
        if ca == "0-17" and "PED" in SERVICES_SITE[site]:
            serv = {"PED": 1.0}
        if ca != "0-17":
            serv = {k: v for k, v in serv.items() if k != "PED"} or serv
        s.loc[g.index, "premier_service"] = choisir(rng, list(serv), list(serv.values()), len(g))
    rum = []
    frac_sc = {"rea": 0.35, "usi": 0.30, "usc": 0.25}
    for i, (idx, row) in enumerate(s.iterrows()):
        etapes = []
        reste = 1.0
        if row["usi"] and row["racine_ghm"][:2] in ("01",) and "UNV" in SERVICES_SITE[site]:
            etapes.append(("UNV", frac_sc["usi"])); reste -= frac_sc["usi"]
        elif row["usi"] and "USIC" in SERVICES_SITE[site]:
            etapes.append(("USIC", frac_sc["usi"])); reste -= frac_sc["usi"]
        chirurgie = SERVICES[row["premier_service"]][1] == "chirurgie"
        if (row["rea"] or row["usc"]) and chirurgie and row["mode_entree"] == "programme":
            # opéré : admis dans le service de chirurgie, puis réanimation ou surveillance continue, puis retour
            etapes.insert(0, (row["premier_service"], 0.05)); etapes.append(("REA", frac_sc["usc"])); reste -= 0.30
        elif row["rea"]:
            etapes.append(("REA", frac_sc["rea"])); reste -= frac_sc["rea"]
        elif row["usc"]:
            etapes.append(("REA", frac_sc["usc"])); reste -= frac_sc["usc"]
        # une partie des décès survient en réanimation : le parcours s'arrête là
        if row["mode_sortie"] == "deces" and any(e[0] == "REA" for e in etapes) and rng.random() < 0.6:
            etapes = [e for e in etapes if e[0] == "REA"][:1]; reste = 0
        # service principal, puis éventuellement un service d'aval (gériatrie, médecine polyvalente)
        u = rng.random()
        prem = row["premier_service"]
        p_mut = 0.25 if row["classe_age"] == "75+" else 0.10
        if u < p_mut and row["duree_j"] > 3 and SERVICES[prem][1] in ("medecine", "chirurgie"):
            aval = [x for x in ("GERIA", "MED_POLY", "MED_INT") if x in SERVICES_SITE[site] and x != prem
                    and not (x == "GERIA" and row["classe_age"] != "75+")]
            if aval:
                etapes.append((prem, reste * 0.6)); etapes.append((aval[0], reste * 0.4)); reste = 0
        if reste > 0:
            etapes.append((prem, reste))
        rum.append((row["id_sejour"], etapes))
    # --- RUM : dates par fraction de la durée totale ; l'UHCD précède le premier service pour une part des urgences
    lignes = []
    uhcd = rng.random(n) < PART_UHCD_AVANT_SERVICE
    for (id_sejour, etapes), (idx, row), passe_uhcd in zip(rum, s.iterrows(), uhcd):
        t = pd.Timestamp(row["date_entree"])
        rang = 1
        if row["mode_entree"] == "urgences" and passe_uhcd:
            d = min(0.6, row["duree_j"] * 0.3)
            lignes.append((site, id_sejour, rang, "URG", t, t + pd.Timedelta(days=d), row["mode_entree"], row["classe_age"], True))
            t += pd.Timedelta(days=d); rang += 1
        total = row["duree_j"] * (1 - (0.3 if row["mode_entree"] == "urgences" and passe_uhcd else 0))
        for service, frac in etapes:
            d = total * frac
            lignes.append((site, id_sejour, rang, service, t, t + pd.Timedelta(days=d), row["mode_entree"], row["classe_age"], False))
            t += pd.Timedelta(days=d); rang += 1
        s.loc[idx, "date_sortie"] = t
    rums = pd.DataFrame(lignes, columns=["nohop", "id_sejour", "rang", "specialite", "date_entree", "date_sortie", "mode_entree", "classe_age", "uhcd"])
    # sortie : heure sur le profil des sorties (sauf soins critiques, à toute heure)
    h = heures_profil(rng, n, PROFIL_HEURE_SORTIE)
    sortie = pd.to_datetime(s["date_sortie"]).dt.normalize() + pd.to_timedelta(h, unit="h")
    entree = pd.to_datetime(s["date_entree"])
    sortie = sortie.where(sortie > entree + pd.Timedelta(hours=2), sortie + pd.Timedelta(days=1))   # jamais avant l'entrée
    s["date_sortie"] = sortie
    s["duree_j"] = (s["date_sortie"] - entree).dt.total_seconds() / 86400
    return s, rums


def generer_venues(rng: np.random.Generator, site: str, annee: int, debut_id: int) -> pd.DataFrame:
    """Les venues aux urgences d'un site et d'une année (sans leur durée, tirée après la tension)."""
    n = int(PASSAGES_PAR_AN[site] * (1 + 0.02 * (annee - 2023)))
    v = pd.DataFrame({"nohop": site, "id_venue": np.arange(debut_id, debut_id + n),
                      "date_entree": dates_profil(rng, n, annee, PROFIL_JOUR, PROFIL_MOIS[annee], PROFIL_HEURE).values})
    v["classe_age"] = choisir(rng, CLASSES_AGE, AGES_URGENCES, n)
    part_f = v["classe_age"].map(PART_F_URGENCES).values
    v["sexe"] = np.where(rng.random(n) < part_f, "2", "1")           # codage RPU : 1 homme, 2 femme
    v["situation"] = ""
    for ca in CLASSES_AGE:
        m = v["classe_age"] == ca
        v.loc[m, "situation"] = choisir(rng, SITUATIONS, SITUATION_PAR_AGE[ca], int(m.sum()))
    # l'hiver hospitalise davantage les âgés
    hiver = v["date_entree"].dt.month.isin([12, 1, 2]) & (v["situation"] == "consultation") & (v["classe_age"] == "75+")
    v.loc[hiver & (rng.random(n) < 0.12), "situation"] = "hospitalisation"
    v["ccmu"] = ""
    for sit in SITUATIONS:
        m = v["situation"] == sit
        v.loc[m, "ccmu"] = choisir(rng, ["1", "2", "3", "4", "5"], CCMU_PAR_SITUATION[sit], int(m.sum()))
    v.loc[rng.random(n) < 0.05, "ccmu"] = ""                               # CCMU manquante
    v["mode_sortie_rpu"] = v["situation"].map({"consultation": "8", "mono_uhcd": "6", "hospitalisation": "6", "transfert": "7", "deces": "9"})
    return v


def durees_venues(rng: np.random.Generator, v: pd.DataFrame, tension_classee: pd.DataFrame, rang2: pd.Series) -> pd.Series:
    """Durée de passage (heures) : médiane par situation × CCMU × âge, allongée par la tension pour les
    hospitalisés, plus un supplément pour ceux qui partent vers un service exceptionnel (rang 2)."""
    t = v.merge(tension_classee[["nohop", "date", "classe_tension"]].assign(date=lambda d: pd.to_datetime(d["date"])),
                left_on=["nohop", pd.to_datetime(v["date_entree"]).dt.normalize()], right_on=["nohop", "date"], how="left")
    med = v["situation"].map(DUREE_MEDIANE_H).values * v["ccmu"].map(FACTEUR_CCMU).fillna(1.0).values \
        * v["classe_age"].map({"0-17": 0.8, "18-64": 0.9, "65-74": 1.1, "75+": 1.35}).values
    hosp = v["situation"].isin(["hospitalisation", "mono_uhcd"]).values
    facteur = np.where(hosp, t["classe_tension"].map(FACTEUR_TENSION_HOSP).fillna(1.0).values, 1.0)
    duree = np.exp(np.log(med * facteur) + rng.normal(0, 0.5, len(v)))
    supplement = np.where(rang2.values, np.exp(np.log(4.0) + rng.normal(0, 0.8, len(v))), 0.0)
    duree = duree + supplement
    duree = np.where(v["situation"].values == "mono_uhcd", np.maximum(duree, 6.5), duree)
    return pd.Series(duree, index=v.index)


def generer_monde(graine: int) -> dict:
    """Toute la réalité fictive : venues, séjours et RUM des deux sites sur les trois années."""
    rng = np.random.default_rng(graine)
    venues, sejours, rums = [], [], []
    id_venue, id_sejour = 1, 1
    for site in (SITE_A, SITE_B):
        lits_conv = sum(SERVICES[s][2] for s in SERVICES_SITE[site] if SERVICES[s][1] in ("medecine", "chirurgie", "obstetrique", "pediatrie"))
        for annee in ANNEES:
            v = generer_venues(rng, site, annee, id_venue); id_venue += len(v)
            # les hospitalisés et mono-UHCD ont un séjour ; sa date d'entrée est la fin du passage (fixée plus bas)
            v["date_sortie"] = v["date_entree"] + pd.to_timedelta(rng.gamma(2.0, 3.0, len(v)), unit="h")  # provisoire
            hosp = v[v["situation"] == "hospitalisation"]
            # entrées programmées : volontairement trop nombreuses (1,3 fois ce qu'il faudrait pour remplir
            # les lits conventionnels) ; calibrer_programme() en retire ensuite service par service
            n_prog = int(lits_conv * 1.3 * 365 / 4.5)
            s, r = generer_sejours(rng, site, annee, hosp, n_prog, id_sejour); id_sejour += len(s)
            # séjours mono-UHCD : un seul RUM d'UHCD, GHM de racine « symptômes »
            mono = v[v["situation"] == "mono_uhcd"]
            m = pd.DataFrame({"id_venue": mono["id_venue"].values, "classe_age": mono["classe_age"].values,
                              "sexe": np.where(mono["sexe"].values == "2", "F", "M"), "date_entree": mono["date_entree"].values,
                              "mode_entree": "urgences", "racine_ghm": choisir(rng, ["23M20", "05M13", "06M08", "01M16"], [0.5, 0.2, 0.2, 0.1], len(mono)),
                              "nohop": site, "id_sejour": np.arange(id_sejour, id_sejour + len(mono)), "niveau": "1",
                              "mode_sortie": "domicile", "premier_service": "", "rea": False, "usi": False, "usc": False})
            id_sejour += len(mono)
            m["ghm"] = m["racine_ghm"] + m["niveau"]
            m["duree_j"] = np.clip(rng.gamma(2.5, 0.35, len(mono)), 0.3, 3.0)
            m["date_sortie"] = pd.to_datetime(m["date_entree"]) + pd.to_timedelta(m["duree_j"], unit="D")
            rm = pd.DataFrame({"nohop": site, "id_sejour": m["id_sejour"], "rang": 1, "specialite": "URG", "date_entree": m["date_entree"],
                               "date_sortie": m["date_sortie"], "mode_entree": "urgences", "classe_age": m["classe_age"], "uhcd": True})
            venues.append(v); sejours += [s, m]; rums += [r, rm]
    monde = {"venues": pd.concat(venues, ignore_index=True), "sejours": pd.concat(sejours, ignore_index=True),
             "rum": pd.concat(rums, ignore_index=True), "rng": rng}
    return calibrer_programme(monde)


OCCUPATION_VISEE = {"medecine": 0.90, "chirurgie": 0.82, "obstetrique": 0.75, "pediatrie": 0.70}


def calibrer_programme(monde: dict) -> dict:
    """Éclaircit les entrées programmées, site par site et service par service, pour que le nombre
    moyen de présents approche lits × occupation visée du type de service, une fois comptés
    les patients venus des urgences (qu'on ne touche pas)."""
    rng, s, rum = monde["rng"], monde["sejours"], monde["rum"]
    passages = rum[~rum["uhcd"]].merge(s[["id_sejour", "premier_service"]], on="id_sejour")
    passages["duree_j"] = (pd.to_datetime(passages["date_sortie"]) - pd.to_datetime(passages["date_entree"])).dt.total_seconds() / 86400
    jours = 365 * len(ANNEES)
    garder = pd.Series(True, index=s.index)
    for site in (SITE_A, SITE_B):
        p = passages[passages["nohop"] == site]
        presents = p.groupby(["specialite", "mode_entree"])["duree_j"].sum().unstack(fill_value=0) / jours
        for service in SERVICES_SITE[site]:
            type_ = SERVICES[service][1]
            if type_ not in OCCUPATION_VISEE or service not in presents.index:
                continue
            cible_ = SERVICES[service][2] * OCCUPATION_VISEE[type_]
            urg, prog = presents.loc[service].get("urgences", 0.0), presents.loc[service].get("programme", 0.0)
            # les séjours programmés dont ce service est le premier : on en garde la part qui comble l'écart
            part = float(np.clip((cible_ - urg) / prog, 0.02, 1.0)) if prog > 0 else 1.0
            m = (s["nohop"] == site) & (s["mode_entree"] == "programme") & (s["premier_service"] == service)
            garder[m] = rng.random(int(m.sum())) < part
    s = s[garder]
    monde["sejours"] = s
    monde["rum"] = rum[rum["id_sejour"].isin(s["id_sejour"])]
    return monde


# ---------------------------------------------------------------------------
# 3. L'extraction : les mêmes fonctions que le notebook
# ---------------------------------------------------------------------------

def ecrire(dossier: Path, graine: int) -> None:
    configurer(seuil_effectif=SEUIL, seuil_lissage=SEUIL_LISSAGE, part_min_service_habituel=PART_MIN,
               colonnes_site=("nohop",), colonnes_saison=("saison",), bornes_age=(17, 64, 74), bornes_age_duree=(59,))
    dossier.mkdir(parents=True, exist_ok=True)
    monde = generer_monde(graine)
    rng = monde["rng"]
    v, s, rum = monde["venues"], monde["sejours"], monde["rum"]
    rum_hors_uhcd = rum[~rum["uhcd"]].drop(columns="uhcd")
    SITE = ["nohop"]

    def exporter(table, nom, colonnes):
        t = table[table["nohop"] == SITE_A] if "nohop" in table.columns else table
        t[colonnes].to_csv(dossier / nom, index=False)
        print(f"{nom:32s} {len(t):7d} lignes")

    # --- séjours chaînés aux venues : nb_rum, nb_rum_hors_uhcd, ghm sur la venue
    par_sejour = rum.groupby("id_sejour").agg(nb_rum=("rang", "size"), nb_rum_hors_uhcd=("uhcd", lambda x: int((~x).sum())))
    lien = s[s["id_venue"].notna()][["id_venue", "id_sejour", "ghm"]].merge(par_sejour, left_on="id_sejour", right_index=True)
    v = v.merge(lien, on="id_venue", how="left")
    v["nas"] = v["id_sejour"]
    v["periode"] = pd.to_datetime(v["date_entree"]).dt.month
    v["saison"] = saison(v["date_entree"])

    # --- tension (séjours de l'année, présents venus des urgences) et classes
    sej_tension = s.assign(urgences=(s["mode_entree"] == "urgences").astype(int))
    tension = tension_journaliere(sej_tension, f"{ANNEES[0]}-01-01", f"{ANNEE_RECENTE}-12-31", contexte=SITE)
    tension_ref = tension[tension["date"].dt.year == ANNEE_RECENTE]
    bornes = classes_tension_depuis_tension(tension_ref, contexte=SITE)
    tension_classee = classer_tension(tension, bornes, contexte=SITE)

    # --- tables de pratiques, multi-sites (pour connaître le rang des premiers services)
    premiers = rum_hors_uhcd.sort_values(["id_sejour", "rang"]).drop_duplicates("id_sejour")[["id_sejour", "specialite"]]
    premiers = premiers.merge(s[["id_sejour", "racine_ghm", "classe_age", "nohop", "mode_entree"]], on="id_sejour")
    eff_premier = premiers.groupby(["racine_ghm", "classe_age", "specialite"]).size().reset_index(name="n")
    premier_service = premier_service_depuis_effectifs(eff_premier, seuil=SEUIL, part_min=PART_MIN, contexte=[])
    exporter(premier_service, "premier_service_racine.csv", ["racine_ghm", "classe_age", "specialite", "part", "rang"])

    effectifs_tp = effectifs_passage(etapes_sejours(rum_hors_uhcd, contexte=SITE), contexte=SITE)
    tp = table_passage_depuis_effectifs(effectifs_tp, seuil=SEUIL, part_min=PART_MIN, contexte=[])
    exporter(tp, "table_passage.csv", ["niveau", "service", "precedent", "classe_age", "mode_entree", "suite", "part", "rang"])

    # --- durées des venues : connues maintenant que la tension et les rangs existent
    vp = premiers.rename(columns={"specialite": "premier_service"})[["id_sejour", "premier_service"]]
    v = v.merge(vp, left_on="nas", right_on="id_sejour", how="left", suffixes=("", "_vp")).drop(columns=["id_sejour_vp"])
    # rang 2 « réel » : le service choisi pèse moins de 10 % dans la pratique du site pour cette racine × âge
    pratique = premiers[premiers["nohop"] == SITE_A].groupby(["racine_ghm", "classe_age", "specialite"]).size().rename("n").reset_index()
    pratique["part"] = pratique["n"] / pratique.groupby(["racine_ghm", "classe_age"])["n"].transform("sum")
    v = v.merge(s[["id_sejour", "racine_ghm"]].rename(columns={"id_sejour": "nas", "racine_ghm": "racine_sej"}), on="nas", how="left")
    v = v.merge(pratique[["racine_ghm", "classe_age", "specialite", "part"]].rename(
        columns={"racine_ghm": "racine_sej", "specialite": "premier_service", "part": "part_pratique"}),
        on=["racine_sej", "classe_age", "premier_service"], how="left")
    rang2 = (v["situation"] == "hospitalisation") & (v["part_pratique"] < PART_MIN)
    duree_h = durees_venues(rng, v, tension_classee, rang2)
    v["date_sortie"] = pd.to_datetime(v["date_entree"]) + pd.to_timedelta(duree_h, unit="h")
    # la date d'entrée du séjour venu des urgences = fin du passage : on décale les RUM et le séjour
    decal = v.loc[v["nas"].notna(), ["nas", "date_sortie"]].rename(columns={"nas": "id_sejour", "date_sortie": "nouvelle_entree"})
    s = s.merge(decal, on="id_sejour", how="left")
    delta = (pd.to_datetime(s["nouvelle_entree"]) - pd.to_datetime(s["date_entree"])).fillna(pd.Timedelta(0))
    s["date_entree"] = pd.to_datetime(s["date_entree"]) + delta
    s["date_sortie"] = pd.to_datetime(s["date_sortie"]) + delta
    rum = rum.merge(s[["id_sejour"]].assign(delta=delta.values), on="id_sejour", how="left")
    rum["date_entree"] = pd.to_datetime(rum["date_entree"]) + rum["delta"]
    rum["date_sortie"] = pd.to_datetime(rum["date_sortie"]) + rum["delta"]
    rum_hors_uhcd = rum[~rum["uhcd"]].drop(columns=["uhcd", "delta"])
    v_ref = v[pd.to_datetime(v["date_entree"]).dt.year == ANNEE_RECENTE]
    s_ref = s[(pd.to_datetime(s["date_entree"]).dt.year == ANNEE_RECENTE)]

    # --- structure et spécialités
    presents = (passages_specialite(rum_hors_uhcd[(rum_hors_uhcd["nohop"] == SITE_A) & (pd.to_datetime(rum_hors_uhcd["date_entree"]).dt.year == ANNEE_RECENTE)], contexte=SITE)
                .groupby("specialite")["duree_j"].sum() / NB_JOURS).round(1)
    services = pd.DataFrame([{"service": c, "libelle": SERVICES[c][0], "specialite": c, "lits": SERVICES[c][2],
                              "soins_critiques": ",".join(SERVICES[c][3]) if SERVICES[c][3] else None,
                              "age_min": SERVICES[c][4], "age_max": SERVICES[c][5], "sexe": SERVICES[c][6],
                              "presents_vises": presents.get(c) if SERVICES[c][1] in ("medecine", "chirurgie", "obstetrique", "pediatrie") else None}
                             for c in SERVICES_SITE[SITE_A]])
    bloc_services = yaml.safe_load(services_vers_yaml(services))["services"]
    for b in bloc_services.values():
        for k in ("presents_vises",):
            if k in b:
                b[k] = float(b[k])
        for k in ("age_min", "age_max"):
            if k in b:
                b[k] = int(float(b[k]))
    structure = {
        "etablissement": "HOPITAL_FICTIF_480",
        "description": "Hôpital fictif d'environ 480 lits ; paramètres fabriqués par scripts/fabriquer_parametres_fictifs.py "
                       f"(graine {graine}) à partir d'une réalité inventée, avec les fonctions d'extraction du module",
        "debut": "2026-01-01",
        "classes_age": CLASSES_AGE, "classes_age_duree": ["0-59", "60+"],
        "services": bloc_services,
        "urgences": {"service": "URG", "passages_par_an": int(len(v_ref[v_ref["nohop"] == SITE_A])),
                     "saison": saisons_completes(ANNEES)[-1], "seuil_uhcd_h": 6, "delai_transfert_h": 24},
        "lits": {"delai_report_programme_h": 8, "heure_releve_tension": 8,
                 "priorites": {"sortie_soins_critiques": 0, "urgences": 1, "mutation": 1, "programme": 2}},
        "sorties": {"services_sans_effet_weekend": ["REA", "USIC", "UNV", "URG"]},
        "population": {"probabilite_retour": {"0-17": 0.10, "18-64": 0.12, "65-74": 0.20, "75+": 0.28}},
        "simulation": {"duree_jours": 365, "chauffe_jours": 42, "marge_fin_jours": 60, "graine": 42},
        "autorisations_soins_critiques": {"rea": ["01A", "01B"], "usi": ["02A", "02E", "02F", "18"], "usc": ["03A", "03B"]},
    }
    with open(dossier / "structure.yaml", "w", encoding="utf-8") as f:
        yaml.safe_dump(structure, f, allow_unicode=True, sort_keys=False)
    print(f"{'structure.yaml':32s} {len(bloc_services):7d} services")
    pd.DataFrame([{"specialite": c, "libelle": SERVICES[c][0], "type": SERVICES[c][1]} for c in SERVICES_SITE[SITE_A]]) \
        .to_csv(dossier / "specialites.csv", index=False)
    rgp = pd.DataFrame([{"ghm": r + n, "racine_ghm": r, "niveau": n, "libelle": f"{RACINES[r][0]}, niveau {n}",
                         "ga": "", "gp": "", "da": RACINES[r][1]} for r in RACINES for n in NIVEAUX])
    rgp.to_csv(dossier / "regroupements_ghm.csv", index=False)

    # --- tables par GHM (les deux sites, trois ans : une « base nationale »)
    # classe d'âge des durées (0-59 / 60+) : les 18-64 sont 60+ dans 8 % des cas (60 à 64 ans)
    s["classe_age_duree"] = np.where(s["classe_age"].isin(["65-74", "75+"]), "60+", "0-59")
    adultes = s["classe_age"] == "18-64"
    s.loc[adultes, "classe_age_duree"] = np.where(rng.random(int(adultes.sum())) < 0.08, "60+", "0-59")
    sejours_ghm = s[s["premier_service"] != ""]            # hors mono-UHCD
    eff = sejours_ghm.groupby(["racine_ghm", "classe_age", "niveau"]).size().reset_index(name="n")
    niveaux = table_proportions(eff, ["racine_ghm", "classe_age"], ["niveau"], replis=[["racine_ghm"]], seuil=SEUIL, contexte=[])
    exporter(niveaux, "niveaux.csv", ["racine_ghm", "classe_age", "niveau", "part"])

    d = sejours_ghm.assign(duree_e=np.floor(sejours_ghm["duree_j"]).astype(int))
    d["duree_j_cat"] = np.where(d["duree_e"] > 60, "60+", d["duree_e"].astype(str))
    eff = d.groupby(["ghm", "racine_ghm", "classe_age_duree", "duree_j_cat"]).agg(n=("duree_e", "size"), total=("duree_e", lambda x: float(x[x > 60].sum()))).reset_index()
    eff = eff.rename(columns={"duree_j_cat": "duree_j"})
    durees_ghm = table_proportions(eff, ["ghm", "racine_ghm", "classe_age_duree"], ["duree_j"],
                                   replis=[["ghm", "racine_ghm"], ["racine_ghm"]], seuil=SEUIL, contexte=[])
    durees_ghm["ghm"] = durees_ghm["ghm"].where(durees_ghm["ghm"] != "", durees_ghm["racine_ghm"])
    m60 = eff[eff["duree_j"] == "60+"].groupby("ghm").agg(n=("n", "sum"), total=("total", "sum"))
    durees_ghm = durees_ghm.merge((m60["total"] / m60["n"]).round(1).rename("moyenne_60plus"), left_on="ghm", right_index=True, how="left")
    durees_ghm.loc[durees_ghm["duree_j"] != "60+", "moyenne_60plus"] = np.nan
    exporter(durees_ghm, "durees_ghm.csv", ["ghm", "classe_age_duree", "duree_j", "part", "moyenne_60plus"])

    def parts_sc(bloc):
        g = sejours_ghm.groupby(bloc).agg(n=("rea", "size"), part_rea=("rea", "mean"), part_usi=("usi", "mean"), part_usc=("usc", "mean")).reset_index()
        return g
    fin = parts_sc(["ghm", "classe_age_duree"]); par_ghm = parts_sc(["ghm"]); par_racine = parts_sc(["racine_ghm"])
    sc = soins_critiques_depuis_effectifs(fin, par_ghm, par_racine, seuil=SEUIL, contexte=[])
    exporter(sc, "soins_critiques_ghm.csv", ["ghm", "classe_age_duree", "part_rea", "part_usi", "part_usc"])

    eff = sejours_ghm.groupby(["racine_ghm", "sexe"]).size().reset_index(name="n")
    exporter(normaliser(eff, ["racine_ghm"], seuil=SEUIL, contexte=[]), "sexe_racine.csv", ["racine_ghm", "sexe", "part"])

    eff = sejours_ghm.groupby(["racine_ghm", "niveau", "classe_age", "mode_sortie"]).size().reset_index(name="n")
    sorties = table_proportions(eff, ["racine_ghm", "niveau", "classe_age"], ["mode_sortie"], replis=[["racine_ghm", "niveau"], ["racine_ghm"]], seuil=SEUIL, contexte=[])
    exporter(sorties, "sorties.csv", ["racine_ghm", "niveau", "classe_age", "mode_sortie", "part"])

    # --- casemix (site A, programmé) et durées de passage (site A, année de référence)
    prog = premiers[(premiers["nohop"] == SITE_A) & (premiers["mode_entree"] == "programme")]
    eff = prog.groupby(["nohop", "specialite", "classe_age", "racine_ghm"]).size().reset_index(name="n")
    casemix = normaliser(eff, ["specialite"], ["classe_age", "racine_ghm"], seuil=SEUIL, contexte=SITE)
    exporter(casemix, "casemix.csv", ["specialite", "classe_age", "racine_ghm", "part"])

    rum_ref = rum_hors_uhcd[(rum_hors_uhcd["nohop"] == SITE_A) & (pd.to_datetime(rum_hors_uhcd["date_entree"]).dt.year == ANNEE_RECENTE)]
    passages = passages_specialite(rum_ref, contexte=SITE)
    exporter(durees_passage_depuis_passages(passages, seuil=SEUIL, contexte=SITE), "durees_passage.csv",
             ["specialite", "mode_entree", "classe_age", "mediane_j", "q1_j", "q3_j", "moyenne_j"])

    # --- urgences (site A) : venues par saison, arrivées, patients, gravité, tension, durées, supplément
    venues_par_annee = [v[pd.to_datetime(v["date_entree"]).dt.year == a] for a in ANNEES]
    eff = additionner([effectifs_venues(x, contexte=SITE) for x in venues_par_annee], ["saison", *REGLAGES.colonnes_venues])
    venues = venues_depuis_effectifs(eff, seuil=SEUIL, contexte=SITE)
    venues = venues[venues["saison"].isin(saisons_completes(ANNEES))]
    exporter(venues, "venues_urgences.csv", ["saison", "periode", "classe_age", "situation", "gravite", "racine_ghm", "part"])

    eff = additionner([effectifs_arrivees(x, contexte=SITE) for x in venues_par_annee], ["saison", *COLONNES_PROFIL_ARRIVEES])
    arrivees = profil_depuis_effectifs(eff, contexte=SITE)
    arrivees = arrivees[arrivees["saison"].isin(saisons_completes(ANNEES))]
    exporter(arrivees, "profil_arrivees_urgences.csv", ["saison", "dimension", "jour_semaine", "heure", "mois", "part"])

    exporter(patients_urgences_depuis_effectifs(effectifs_patients_urgences(v_ref, contexte=SITE), contexte=SITE),
             "profil_patients_urgences.csv", ["classe_age", "sexe", "part"])
    exporter(gravite_depuis_effectifs(effectifs_gravite(v_ref, contexte=SITE), seuil=SEUIL, contexte=SITE),
             "gravite_urgences.csv", ["classe_age", "situation", "tranche_h", "gravite", "part"])
    exporter(bornes, "classes_tension.csv", ["classe", "borne_inf", "borne_sup"])

    tension_ref_classee = tension_classee[pd.to_datetime(tension_classee["date"]).dt.year == ANNEE_RECENTE]
    eff = effectifs_durees_urgences(v_ref, tension_ref_classee, premier_service, contexte=SITE)
    exporter(durees_urgences_depuis_effectifs(eff, seuil=SEUIL, contexte=SITE), "durees_passage_urgences.csv",
             ["situation", "gravite", "classe_age", "classe_tension", "tranche_h", "part"])
    eff = effectifs_supplement_rang2(v_ref, tension_ref_classee, premier_service, contexte=SITE)
    exporter(supplement_depuis_effectifs(eff, seuil=SEUIL, contexte=SITE), "supplement_rang2.csv", ["classe_tension", "tranche_h", "part"])

    # --- entrées programmées et sorties (site A, année de référence, sur les séjours)
    s_a = s_ref[s_ref["nohop"] == SITE_A]
    entrees = s_a[s_a["mode_entree"] == "programme"]
    exporter(profil_depuis_effectifs(effectifs_profil_semaine(entrees, entrees["date_entree"], contexte=SITE), contexte=SITE),
             "profil_entrees_programmees.csv", ["dimension", "jour_semaine", "heure", "part"])
    exporter(profil_depuis_effectifs(effectifs_profil_semaine(s_a, s_a["date_sortie"], contexte=SITE), contexte=SITE),
             "profil_sorties.csv", ["dimension", "jour_semaine", "heure", "part"])

    # --- cibles
    lignes = []
    p_a = passages[passages["nohop"] == SITE_A]
    for spec, g in p_a.groupby("specialite"):
        lignes.append(cible("presents_moyens", g["duree_j"].sum() / NB_JOURS, service=spec))
        lignes.append(cible("dms", g["duree_j"].mean(), service=spec))
        lignes.append(cible("part_urgences", g["mode_entree"].eq("urgences").mean(), 0.05, absolue=True, service=spec))
    lignes.append(cible("nb_etapes", p_a.groupby("id_sejour").size().mean(), 0.1, absolue=True))
    va = v_ref[v_ref["nohop"] == SITE_A]
    situations = va["situation"].value_counts(normalize=True)
    for sit in ["hospitalisation", "mono_uhcd", "transfert"]:
        lignes.append(cible(f"part_{sit}", situations.get(sit, 0), 0.02, absolue=True))
    lignes.append(cible("part_rang2", float(rang2[v.index.isin(va.index)].sum() / max((va["situation"] == "hospitalisation").sum(), 1)), 0.03, absolue=True))
    lignes.append(cible("tension_moyenne", tension_ref.loc[tension_ref["nohop"] == SITE_A, "tension"].mean()))
    pd.DataFrame(lignes)[["indicateur", "service", "classe_tension", "observe", "min", "max"]].to_csv(dossier / "cibles.csv", index=False)
    print(f"{'cibles.csv':32s} {len(lignes):7d} lignes")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dossier", default="parametres/hopital_type")
    ap.add_argument("--graine", type=int, default=42)
    args = ap.parse_args()
    dossier = Path(args.dossier)
    ecrire(dossier, args.graine)
    print("\nVérification du dossier :")
    ok = verifier_dossier(dossier, [])
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
