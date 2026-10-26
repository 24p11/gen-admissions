"""Lecture et vérification des fichiers de paramètres (YAML + CSV).

Tout ce que le simulateur doit savoir sur l'hôpital passe par ici. Le reste
du code ne lit jamais de fichier directement : il reçoit un objet
``Parametres`` déjà vérifié.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from .distributions import LoiDuree, ProfilHoraire, parametres_lognormale

JOURS_SEMAINE = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
SORTIES = ("domicile", "SSR", "EHPAD", "transfert", "deces")


class ErreurParametres(ValueError):
    """Un fichier de paramètres est absent, mal formé ou incohérent."""


@dataclass
class Service:
    code: str
    libelle: str
    type: str
    lits: int
    hebergement: list[str] = field(default_factory=list)
    age_min: int | None = None
    age_max: int | None = None
    sexe: str | None = None

    def accepte(self, age: int, sexe: str) -> bool:
        """Le service peut-il accueillir ce patient (contraintes d'âge et de sexe) ?"""
        if self.age_min is not None and age < self.age_min:
            return False
        if self.age_max is not None and age > self.age_max:
            return False
        if self.sexe is not None and sexe != self.sexe:
            return False
        return True


@dataclass
class Parametres:
    dossier: Path
    structure: dict
    services: dict[str, Service]
    # urgences
    profil_heure_urgences: np.ndarray        # 24 poids normalisés (moyenne = 1)
    profil_jour_urgences: np.ndarray         # 7 poids (lundi=0), moyenne = 1
    profil_mois_urgences: np.ndarray         # 12 poids (janvier=0), moyenne = 1
    profil_patients_urgences: pd.DataFrame   # classe_age, sexe, probabilite
    gravite_urgences: dict[str, tuple[list[int], np.ndarray]]
    issues_urgences: dict[tuple[str, int], tuple[list[str], np.ndarray]]
    durees_passage_urgences: dict[str, tuple[float, float]]  # issue -> (mu, sigma) en heures
    # programmé
    entrees_programmees: dict[str, float]    # service -> entrées par semaine
    profil_jour_programme: np.ndarray        # 7 poids normalisés (somme = 1)
    profil_heure_programme: ProfilHoraire
    profil_patients_programme: dict[str, tuple[list[tuple[str, str]], np.ndarray]]
    # parcours
    premier_service: dict[tuple[str, str, str], tuple[list[str], np.ndarray, int]]
    catalogue: pd.DataFrame
    table_passage: dict[str, dict[tuple, tuple[list[str], np.ndarray, int]]]
    durees_sejour: dict[str, dict[tuple, LoiDuree]]
    profil_sorties: ProfilHoraire
    cibles: pd.DataFrame

    # ---- accès pratiques -------------------------------------------------
    @property
    def debut(self) -> datetime:
        return datetime.fromisoformat(str(self.structure["debut"]))

    @property
    def classes_age(self) -> list[str]:
        return list(self.structure["classes_age"])

    @property
    def urgences(self) -> dict:
        return self.structure["urgences"]

    @property
    def regles(self) -> dict:
        return self.structure["regles"]

    @property
    def simulation(self) -> dict:
        return self.structure["simulation"]

    @property
    def seuil(self) -> int:
        return int(self.structure.get("confidentialite", {}).get("seuil_effectif", 1))

    @property
    def services_avec_lits(self) -> list[str]:
        return [c for c, s in self.services.items() if s.lits > 0]

    def classe_age(self, age: int) -> str:
        """Retrouve la tranche d'âge (par exemple « 65-79 ») d'un âge exact."""
        for classe in self.classes_age:
            if classe.endswith("+"):
                if age >= int(classe[:-1]):
                    return classe
            else:
                a, b = classe.split("-")
                if int(a) <= age <= int(b):
                    return classe
        raise ErreurParametres(f"Aucune classe d'âge pour l'âge {age}")

    def bornes_classe_age(self, classe: str) -> tuple[int, int]:
        if classe.endswith("+"):
            return int(classe[:-1]), 99
        a, b = classe.split("-")
        return int(a), int(b)


# ---------------------------------------------------------------------------
# Lecture
# ---------------------------------------------------------------------------
def _lire_csv(dossier: Path, nom: str) -> pd.DataFrame:
    chemin = dossier / nom
    if not chemin.exists():
        raise ErreurParametres(f"Fichier manquant : {chemin}")
    return pd.read_csv(chemin, dtype=str, keep_default_na=False)


def _profil_dimension(df: pd.DataFrame, dimension: str, cles: list[str], normaliser_moyenne: bool) -> np.ndarray:
    part = df[df["dimension"] == dimension]
    poids = np.zeros(len(cles))
    for valeur, w in zip(part["valeur"], part["poids"]):
        if valeur not in cles:
            raise ErreurParametres(f"Valeur inconnue {valeur!r} pour la dimension {dimension!r}")
        poids[cles.index(valeur)] = float(w)
    if poids.sum() <= 0:
        raise ErreurParametres(f"Profil vide pour la dimension {dimension!r}")
    return poids / poids.mean() if normaliser_moyenne else poids / poids.sum()


def _verifier_somme(nom: str, cle, p: np.ndarray) -> None:
    if abs(p.sum() - 1.0) > 0.02:
        raise ErreurParametres(f"{nom} : les probabilités de la case {cle} somment à {p.sum():.3f} au lieu de 1")


def charger(dossier: str | Path) -> Parametres:
    """Charge et vérifie tous les fichiers de paramètres d'un dossier."""
    dossier = Path(dossier)
    with open(dossier / "structure.yaml", encoding="utf-8") as f:
        structure = yaml.safe_load(f)

    # --- services ---------------------------------------------------------
    services: dict[str, Service] = {}
    for code, d in structure["services"].items():
        services[code] = Service(code=code, libelle=d.get("libelle", code), type=d["type"], lits=int(d.get("lits", 0)),
                                 hebergement=list(d.get("hebergement", [])), age_min=d.get("age_min"),
                                 age_max=d.get("age_max"), sexe=d.get("sexe"))
    for s in services.values():
        for h in s.hebergement:
            if h not in services or services[h].lits <= 0:
                raise ErreurParametres(f"Service d'hébergement inconnu ou sans lits : {h} (pour {s.code})")
    codes_lits = {c for c, s in services.items() if s.lits > 0}

    # --- urgences -----------------------------------------------------------
    prof = _lire_csv(dossier, "profil_arrivees_urgences.csv")
    heure_urg = _profil_dimension(prof, "heure", [str(h) for h in range(24)], True)
    jour_urg = _profil_dimension(prof, "jour_semaine", JOURS_SEMAINE, True)
    mois_urg = _profil_dimension(prof, "mois", [str(m) for m in range(1, 13)], True)

    pat_urg = _lire_csv(dossier, "profil_patients_urgences.csv")
    pat_urg["probabilite"] = pat_urg["probabilite"].astype(float)
    _verifier_somme("profil_patients_urgences", "total", pat_urg["probabilite"].to_numpy())

    grav = _lire_csv(dossier, "gravite_urgences.csv")
    gravite: dict[str, tuple[list[int], np.ndarray]] = {}
    for classe, grp in grav.groupby("classe_age"):
        p = grp["probabilite"].astype(float).to_numpy()
        _verifier_somme("gravite_urgences", classe, p)
        gravite[classe] = ([int(g) for g in grp["gravite"]], p / p.sum())

    iss = _lire_csv(dossier, "issues_urgences.csv")
    issues: dict[tuple[str, int], tuple[list[str], np.ndarray]] = {}
    for (classe, g), grp in iss.groupby(["classe_age", "gravite"]):
        p = grp["probabilite"].astype(float).to_numpy()
        _verifier_somme("issues_urgences", (classe, g), p)
        issues[(classe, int(g))] = (list(grp["issue"]), p / p.sum())

    dpu = _lire_csv(dossier, "durees_passage_urgences.csv")
    durees_passage = {r["issue"]: parametres_lognormale(float(r["mediane_h"]), float(r["q1_h"]), float(r["q3_h"]))
                      for _, r in dpu.iterrows()}

    # --- programmé ------------------------------------------------------------
    ep = _lire_csv(dossier, "entrees_programmees.csv")
    entrees = {r["service"]: float(r["entrees_par_semaine"]) for _, r in ep.iterrows()}
    for s in entrees:
        if s not in codes_lits:
            raise ErreurParametres(f"entrees_programmees : service inconnu ou sans lits : {s}")
    prof_p = _lire_csv(dossier, "profil_entrees_programmees.csv")
    jour_prog = _profil_dimension(prof_p, "jour_semaine", JOURS_SEMAINE, False)
    heures = prof_p[prof_p["dimension"] == "heure"]
    heure_prog = ProfilHoraire({int(v): float(w) for v, w in zip(heures["valeur"], heures["poids"])})
    ppp = _lire_csv(dossier, "profil_patients_programme.csv")
    pat_prog: dict[str, tuple[list[tuple[str, str]], np.ndarray]] = {}
    for service, grp in ppp.groupby("service"):
        p = grp["probabilite"].astype(float).to_numpy()
        _verifier_somme("profil_patients_programme", service, p)
        pat_prog[service] = (list(zip(grp["classe_age"], grp["sexe"])), p / p.sum())
    for s in entrees:
        if s not in pat_prog:
            raise ErreurParametres(f"profil_patients_programme : aucun profil pour le service {s}")

    # --- parcours -------------------------------------------------------------
    prem = _lire_csv(dossier, "premier_service.csv")
    premier: dict[tuple[str, str, str], tuple[list[str], np.ndarray, int]] = {}
    for (mode, classe, sexe), grp in prem.groupby(["mode_entree", "classe_age", "sexe"]):
        p = grp["probabilite"].astype(float).to_numpy()
        _verifier_somme("premier_service", (mode, classe, sexe), p)
        premier[(mode, classe, sexe)] = (list(grp["service"]), p / p.sum(), int(float(grp["effectif_case"].iloc[0])))

    cat = _lire_csv(dossier, "catalogue_trajectoires.csv")
    cat["effectif"] = cat["effectif"].astype(int)
    cat["etapes"] = cat["trajectoire"].str.split(" > ")
    cat["premier_service"] = cat["etapes"].map(lambda e: e[0])
    for etapes in cat["etapes"]:
        for s in etapes:
            if s not in codes_lits:
                raise ErreurParametres(f"catalogue_trajectoires : service inconnu {s}")

    tp = _lire_csv(dossier, "table_passage.csv")
    table: dict[str, dict[tuple, tuple[list[str], np.ndarray, int]]] = {}
    cles_niveau = {"complet": ["service", "precedent", "classe_age", "mode_entree"],
                   "sans_precedent": ["service", "classe_age", "mode_entree"],
                   "sans_age": ["service", "mode_entree"], "service": ["service"]}
    for niveau, cles in cles_niveau.items():
        table[niveau] = {}
        part = tp[tp["niveau"] == niveau]
        for cle, grp in part.groupby(cles):
            cle = cle if isinstance(cle, tuple) else (cle,)
            p = grp["probabilite"].astype(float).to_numpy()
            _verifier_somme(f"table_passage[{niveau}]", cle, p)
            suites = list(grp["suite"])
            for s in suites:
                if s not in codes_lits and s not in SORTIES:
                    raise ErreurParametres(f"table_passage : suite inconnue {s}")
            table[niveau][cle] = (suites, p / p.sum(), int(float(grp["effectif_case"].iloc[0])))

    ds = _lire_csv(dossier, "durees_sejour.csv")
    durees: dict[str, dict[tuple, LoiDuree]] = {"complet": {}, "sans_age": {}, "service": {}}
    for _, r in ds.iterrows():
        loi = LoiDuree.depuis_quartiles(float(r["mediane_j"]), float(r["q1_j"]), float(r["q3_j"]), float(r["part_moins_1j"]))
        if r["niveau"] == "complet":
            durees["complet"][(r["service"], r["mode_entree"], r["classe_age"])] = loi
        elif r["niveau"] == "sans_age":
            durees["sans_age"][(r["service"], r["mode_entree"])] = loi
        else:
            durees["service"][(r["service"],)] = loi
    for s in codes_lits:
        if (s,) not in durees["service"]:
            raise ErreurParametres(f"durees_sejour : aucune durée pour le service {s}")

    ps = _lire_csv(dossier, "profil_sorties.csv")
    profil_sorties = ProfilHoraire({int(h): float(w) for h, w in zip(ps["heure"], ps["poids"])})

    cibles = _lire_csv(dossier, "cibles.csv") if (dossier / "cibles.csv").exists() else pd.DataFrame(
        columns=["indicateur", "service", "valeur_min", "valeur_max"])

    return Parametres(
        dossier=dossier, structure=structure, services=services,
        profil_heure_urgences=heure_urg, profil_jour_urgences=jour_urg, profil_mois_urgences=mois_urg,
        profil_patients_urgences=pat_urg, gravite_urgences=gravite, issues_urgences=issues,
        durees_passage_urgences=durees_passage,
        entrees_programmees=entrees, profil_jour_programme=jour_prog, profil_heure_programme=heure_prog,
        profil_patients_programme=pat_prog,
        premier_service=premier, catalogue=cat, table_passage=table, durees_sejour=durees,
        profil_sorties=profil_sorties, cibles=cibles,
    )
