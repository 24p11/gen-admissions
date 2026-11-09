"""Fabriquer un patient : les tirages des étapes A (urgences), B (programmé) et C (séjour).

Ce module ne connaît ni le temps SimPy, ni les lits : il sait seulement, à partir des
``Parametres``, inventer un patient cohérent — qui il est, ce qui lui arrive aux urgences,
quel séjour il fera. Le moteur l'appellera à chaque arrivée ; les tests le font tourner
des milliers de fois pour vérifier qu'il reproduit les tables.

Les étapes, dans l'ordre du dictionnaire :

- A2 ``patients`` → classe d'âge et sexe (et un âge exact tiré dans la classe) ;
- A3 ``venues`` sachant saison, mois, classe d'âge → situation, gravité CCMU (si pas de
  séjour), racine de GHM (si séjour) ;
- A4 ``durees_urgences`` sachant situation, gravité, classe d'âge, classe de tension → tranche
  de durée, convertie en heures ; les mono-UHCD ont un plancher de ``seuil_uhcd_h`` ;
- A5 ``gravite`` pour les hospitalisés, sachant classe d'âge, situation, tranche de durée ;
- A7 ``premier_service`` sachant racine et classe d'âge → les services candidats et leurs
  rangs (restreints à la structure) ; aucun candidat → transfert ;
- B3 ``casemix`` sachant la spécialité → classe d'âge et racine d'une entrée programmée ;
- C1 à C4 et C7 : sexe, niveau, durée totale, soins critiques, mode de sortie du séjour.

Les conventions de temps : les durées sont en heures (urgences) ou en jours décimaux
(séjours) ; aucune date absolue ici.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import warnings

import numpy as np
import pandas as pd

from .parametres import Ligne, Parametres, _bornes_classe

TRANCHES_H = {"0-1": (0, 1), "1-2": (1, 2), "2-4": (2, 4), "4-6": (4, 6), "6-12": (6, 12), "12-24": (12, 24), "24+": (24, None)}
SITUATIONS_AVEC_SEJOUR = ("hospitalisation", "mono_uhcd")
AGE_MAX = 100


@dataclass
class Patient:
    """Tout ce qui est décidé sur un patient avant qu'il entre dans un lit."""
    origine: str                               # « urgences » ou « programme »
    classe_age: str
    age: int
    sexe: str                                  # « M » / « F »
    # urgences (origine = urgences)
    situation: str = ""                        # consultation, mono_uhcd, hospitalisation, transfert, deces
    gravite: str = ""                          # CCMU 1 à 5, « » si inconnue
    duree_urgences_h: float = 0.0              # temps passé aux urgences avant la sortie ou le lit
    tranche_h: str = ""
    classe_tension: str = ""
    # séjour (situation hospitalisation / mono_uhcd, ou origine programme)
    racine_ghm: str = ""
    niveau: str = ""
    ghm: str = ""
    classe_age_duree: str = ""
    duree_sejour_j: float = 0.0                # durée totale du séjour, en jours décimaux
    soins_critiques: dict[str, bool] = field(default_factory=dict)   # rea, usi, usc
    mode_sortie: str = ""
    # premier service (A7 / B)
    services_candidats: list[tuple[str, float, int]] = field(default_factory=list)   # (service, part, rang)
    service_prevu: str = ""                    # le service tiré sur les candidats (programmé : la spécialité)
    transfert: bool = False                    # aucun service d'accueil dans l'hôpital
    niveau_premier_service: tuple[str, ...] = ()   # le niveau de repli utilisé pour A7
    # identifiants, posés par le moteur
    ipp: int | None = None
    no_visite: int | None = None
    nas: int | None = None

    @property
    def a_sejour(self) -> bool:
        return self.situation in SITUATIONS_AVEC_SEJOUR or self.origine == "programme"

    @property
    def hospitalise(self) -> bool:
        """Entre dans un service (pas un mono-UHCD, qui reste dans les lits des urgences)."""
        return self.origine == "programme" or self.situation == "hospitalisation"


class Tireur:
    """Les tirages, liés à un jeu de paramètres et à un générateur aléatoire."""

    def __init__(self, p: Parametres, rng: np.random.Generator):
        self.p = p
        self.rng = rng
        self.seuil_uhcd_h = float(p.structure.urgences.get("seuil_uhcd_h", 6))
        self._casemix_cache: dict[str, Ligne] = {}

    # -- outils ------------------------------------------------------------

    def age_dans_classe(self, classe_age: str) -> int:
        bas, haut = _bornes_classe(classe_age)
        haut = AGE_MAX if haut is None else haut
        return int(self.rng.integers(bas, haut + 1))

    def classe_age_duree(self, age: int) -> str:
        """La classe d'âge des tables par GHM (0-59 / 60+), déduite de l'âge exact."""
        classes = self.p.structure.classes_age_duree or ["0-59", "60+"]
        for classe in classes:
            bas, haut = _bornes_classe(classe)
            if age >= bas and (haut is None or age <= haut):
                return classe
        return classes[-1]

    def tranche_vers_heures(self, tranche: str, plancher: float = 0.0) -> float:
        """Une durée en heures tirée uniformément dans la tranche (au-delà de 24 h : 24 h + exponentielle de moyenne 12 h)."""
        bas, haut = TRANCHES_H[tranche]
        if haut is None:
            d = 24 + float(self.rng.exponential(12.0))
        else:
            d = bas + float(self.rng.random()) * (haut - bas)
        return max(d, plancher)

    def duree_vers_jours(self, duree_j: str, moyenne_60plus: float | None) -> float:
        """« 3 » → entre 3 et 4 jours ; « 0 » → quelques heures ; « 60+ » → au-delà de 60 jours."""
        if duree_j == "60+":
            moyenne = moyenne_60plus if moyenne_60plus and moyenne_60plus > 60 else 75.0
            return 60 + float(self.rng.exponential(moyenne - 60))
        d = max(0, int(duree_j))
        if d == 0:
            return float(self.rng.uniform(2, 20)) / 24      # séjour sans nuitée : quelques heures
        return d + float(self.rng.random())

    # -- A : une venue aux urgences -----------------------------------------

    def venue_urgences(self, saison: str, mois: int, classe_tension: str) -> Patient:
        p, rng = self.p, self.rng
        classe_age, sexe = p.patients.tirer(rng)                                   # A2
        age = self.age_dans_classe(classe_age)
        situation, gravite, racine = p.venues.tirer(rng, saison=saison, periode=mois, classe_age=classe_age)   # A3
        patient = Patient(origine="urgences", classe_age=classe_age, age=age, sexe=sexe, situation=situation,
                          gravite=gravite, racine_ghm=racine, classe_tension=classe_tension)
        # A4 : la durée de passage, sachant la gravité (non hospitalisés) et la tension du jour
        tranche = p.durees_urgences.tirer(rng, situation=situation, gravite=gravite if situation not in SITUATIONS_AVEC_SEJOUR else "",
                                          classe_age=classe_age, classe_tension=classe_tension)
        patient.tranche_h = tranche
        plancher = self.seuil_uhcd_h if situation == "mono_uhcd" else 0.0
        patient.duree_urgences_h = self.tranche_vers_heures(tranche, plancher)
        if situation in SITUATIONS_AVEC_SEJOUR:
            # A5 : la gravité des hospitalisés, descriptive, tirée en dernier
            ligne = p.gravite.chercher(classe_age=classe_age, situation=situation, tranche_h=tranche)
            patient.gravite = ligne.tirer(rng)[0] if ligne is not None else ""
            self.completer_sejour(patient)
        if situation == "hospitalisation":
            self.premier_service(patient)                                            # A7
        return patient

    def premier_service(self, patient: Patient) -> None:
        """A7 : les services candidats pour cette racine et cette classe d'âge, et le service prévu."""
        p = self.p
        if patient.racine_ghm in p.racines_transferees:
            patient.transfert = True
            return
        ligne: Ligne | None = p.premier_service.chercher(racine_ghm=patient.racine_ghm, classe_age=patient.classe_age)
        if ligne is None:
            patient.transfert = True
            return
        rangs = ligne.annexes.get("rang")
        candidats = []
        for i, (service,) in enumerate(ligne.categories):
            s = p.structure.services.get(service)
            if s is None or not s.accepte(patient.classe_age, patient.sexe):
                continue
            candidats.append((service, float(ligne.parts[i]), int(rangs[i]) if rangs is not None else 1))
        if not candidats:
            patient.transfert = True
            return
        total = sum(c[1] for c in candidats)
        patient.services_candidats = [(s, part / total, rang) for s, part, rang in candidats]
        patient.niveau_premier_service = ligne.niveau
        parts = np.array([c[1] for c in patient.services_candidats])
        patient.service_prevu = patient.services_candidats[int(self.rng.choice(len(parts), p=parts))][0]

    # -- B : une entrée programmée --------------------------------------------

    def casemix_service(self, specialite: str) -> Ligne:
        """B3 : le casemix d'un service, restreint aux classes d'âge et renormalisé.

        Le casemix observé peut contenir des classes d'âge que la structure interdit au service (une
        gériatrie à 75 ans et plus qui a reçu quelques 65-74 ans) : ces lignes sont retirées une fois pour
        toutes. Si rien ne subsiste (service absent du casemix, ou dont toutes les classes sont interdites),
        on se replie sur le casemix de toutes les spécialités restreint aux classes acceptées, avec un
        avertissement.
        """
        if specialite in self._casemix_cache:
            return self._casemix_cache[specialite]
        p = self.p
        service = p.structure.services[specialite]
        ligne = p.casemix.chercher(specialite=specialite)
        cats = [(c, w) for c, w in zip(ligne.categories, ligne.parts) if service.accepte(c[0])] if ligne is not None else []
        if not cats:
            tout: dict[tuple, float] = {}
            for (sp,) in p.casemix.blocs():
                l = p.casemix.chercher(specialite=sp)
                for c, w in zip(l.categories, l.parts):
                    if service.accepte(c[0]):
                        tout[c] = tout.get(c, 0.0) + float(w)
            cats = list(tout.items())
            presentes = sorted({c[0] for c in ligne.categories}) if ligne is not None else []
            warnings.warn(f"casemix de {specialite} : {'absent' if ligne is None else 'classes ' + str(presentes) + ' toutes refusées'} "
                          f"(age_min={service.age_min}, age_max={service.age_max}) ; repli sur le casemix de toutes les spécialités")
        if not cats:
            raise ValueError(f"aucune classe d'âge du casemix n'est acceptée par {specialite} (age_min={service.age_min}, age_max={service.age_max})")
        parts = np.array([w for _, w in cats], dtype=float)
        parts /= parts.sum()
        self._casemix_cache[specialite] = Ligne(categories=[c for c, _ in cats], parts=parts, cumul=np.cumsum(parts))
        return self._casemix_cache[specialite]

    def entree_programmee(self, specialite: str) -> Patient:
        """B3 : un patient programmé dans cette spécialité (classe d'âge et racine depuis le casemix)."""
        p, rng = self.p, self.rng
        service = p.structure.services[specialite]
        classe_age, racine = self.casemix_service(specialite).tirer(rng)
        sexe = service.sexe or p.sexe_racine.tirer(rng, racine_ghm=racine)
        age = self.age_dans_classe(classe_age)
        if service.age_min is not None:
            age = max(age, service.age_min)
        if service.age_max is not None:
            age = min(age, service.age_max)
        patient = Patient(origine="programme", classe_age=classe_age, age=age, sexe=sexe,
                          racine_ghm=racine, service_prevu=specialite, services_candidats=[(specialite, 1.0, 1)])
        self.completer_sejour(patient)
        return patient

    # -- C : le séjour ----------------------------------------------------------

    def completer_sejour(self, patient: Patient) -> None:
        """C1 sexe (si pas encore connu), C2 niveau, C3 durée, C4 soins critiques, C7 mode de sortie."""
        p, rng = self.p, self.rng
        if not patient.sexe:
            patient.sexe = p.sexe_racine.tirer(rng, racine_ghm=patient.racine_ghm)
        patient.niveau = p.niveaux.tirer(rng, racine_ghm=patient.racine_ghm, classe_age=patient.classe_age)
        patient.ghm = patient.racine_ghm + patient.niveau
        patient.classe_age_duree = self.classe_age_duree(patient.age)
        ligne = p.durees_ghm.chercher(ghm=patient.ghm, classe_age_duree=patient.classe_age_duree)
        if ligne is None:                                                         # GHM inconnu : repli racine
            ligne = p.durees_ghm.chercher(ghm=patient.racine_ghm, classe_age_duree=patient.classe_age_duree)
        if ligne is None:
            raise KeyError(f"aucune durée pour le GHM {patient.ghm} ni sa racine")
        categorie = ligne.tirer(rng)
        i = ligne.categories.index(categorie)
        moyenne = ligne.annexes.get("moyenne_60plus")
        patient.duree_sejour_j = self.duree_vers_jours(categorie[0], None if moyenne is None else float(moyenne[i]))
        if patient.situation == "mono_uhcd":
            # un séjour limité à l'UHCD dure le temps du passage aux urgences, pas celui d'un GHM
            patient.duree_sejour_j = patient.duree_urgences_h / 24
        probas = p.soins_critiques.probabilites(patient.ghm, patient.classe_age_duree)
        patient.soins_critiques = {k: bool(rng.random() < v) for k, v in probas.items()}
        patient.mode_sortie = p.sorties.tirer(rng, racine_ghm=patient.racine_ghm, niveau=patient.niveau, classe_age=patient.classe_age)

    # -- règle D, partie tirage ------------------------------------------------

    def supplement_rang2_h(self, classe_tension: str) -> float:
        """L'attente supplémentaire avant de demander un lit dans un service de rang 2 (D4)."""
        tranche = self.p.supplement.tirer(self.rng, classe_tension=classe_tension)
        return self.tranche_vers_heures(tranche)

    def suite_parcours(self, service: str, precedent: str, classe_age: str, mode_entree: str) -> tuple[str, int | None, tuple[str, ...]]:
        """C5 : la suite du parcours après un passage dans ``service`` : (« sortie » ou service suivant,
        rang, niveau de la table utilisé). Le niveau fin d'abord, puis le repli par service."""
        p = self.p
        ligne = p.table_passage.chercher(niveau="complet", service=service, precedent=precedent, classe_age=classe_age, mode_entree=mode_entree)
        if ligne is None:
            ligne = p.table_passage.chercher(niveau="service", service=service)
        if ligne is None:
            return "sortie", None, ()
        categorie = ligne.tirer(self.rng)
        i = ligne.categories.index(categorie)
        rang = ligne.annexes.get("rang")
        r = None if rang is None or pd.isna(rang[i]) else int(rang[i])
        return categorie[0], r, ligne.niveau
