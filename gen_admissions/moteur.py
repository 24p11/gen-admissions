"""Le moteur de simulation : assemble les processus SimPy et fait tourner l'horloge.

Vocabulaire SimPy (voir le guide de lecture) :
- ``env`` est le surveillant : horloge + agenda ;
- chaque générateur de flux et chaque patient est un processus ;
- les lits sont des ressources, gérées dans ``lits.py`` ;
- un ``yield`` est un rendez-vous : « réveille-moi quand… ».

L'unité de temps est l'heure. L'instant 0 correspond à ``structure.debut``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np
import simpy

from .config import SORTIES, Parametres
from .hasard import Hasard
from .lits import GestionnaireLits, Obtention
from .modele import Mouvement, Passage, Patient, Rum, Sejour
from .parcours import ConstructeurParcours, Parcours


@dataclass
class Resultat:
    """Tout ce que la simulation a produit."""

    parametres: Parametres
    graine: int
    debut: datetime
    chauffe_h: float
    fin_collecte_h: float
    fin_simulation_h: float
    passages: list[Passage] = field(default_factory=list)
    sejours: list[Sejour] = field(default_factory=list)
    patients: list[Patient] = field(default_factory=list)
    occupation: list[tuple[float, str, int]] = field(default_factory=list)

    def date(self, heures: float | None) -> datetime | None:
        if heures is None:
            return None
        return self.debut + timedelta(hours=float(heures))


class Hopital:
    """L'hôpital virtuel : un environnement SimPy, des lits, des générateurs de patients."""

    def __init__(self, p: Parametres, graine: int, duree_jours: int, chauffe_jours: int, marge_fin_jours: int) -> None:
        self.p = p
        self.hasard = Hasard(graine)
        self.env = simpy.Environment()
        self.lits = GestionnaireLits(self.env, p, self.hasard["lits"])
        self.constructeur = ConstructeurParcours(p, self.hasard["parcours"], self.hasard["durees"])
        self.debut = p.debut
        self.chauffe_h = chauffe_jours * 24.0
        self.fin_collecte_h = (chauffe_jours + duree_jours) * 24.0
        self.fin_simulation_h = (chauffe_jours + duree_jours + marge_fin_jours) * 24.0
        self.resultat = Resultat(p, graine, self.debut, self.chauffe_h, self.fin_collecte_h, self.fin_simulation_h)
        self.resultat.occupation = self.lits.occupation
        # population de patients connus, rangée par classe d'âge (pour les retours)
        self.population: dict[str, list[Patient]] = {c: [] for c in p.classes_age}
        self._n_patients = self._n_passages = self._n_sejours = 0
        # caches
        self._poids_patients_urg = p.profil_patients_urgences["probabilite"].to_numpy()
        self._profils_patients_urg = list(zip(p.profil_patients_urgences["classe_age"], p.profil_patients_urgences["sexe"]))
        self._services_weekend = set(p.structure.get("sorties", {}).get("services_sans_effet_weekend", []))
        self._facteur_weekend = float(p.structure.get("sorties", {}).get("facteur_weekend", 1.0))
        self._p_retour = p.structure.get("population", {}).get("probabilite_retour", {})

    # ------------------------------------------------------------------
    # Outils
    # ------------------------------------------------------------------
    def date(self, t: float | None = None) -> datetime:
        return self.debut + timedelta(hours=float(self.env.now if t is None else t))

    def _nouveau_patient(self, classe_age: str, sexe: str, rng: np.random.Generator, service: str | None = None) -> Patient:
        """Crée un patient, ou fait revenir un patient déjà connu (réhospitalisation, retour aux urgences).

        Si ``service`` est donné (admission programmée), l'âge exact respecte
        les contraintes du service (par exemple 75 ans et plus en gériatrie).
        """
        a, b = self.p.bornes_classe_age(classe_age)
        if service is not None:
            s = self.p.services[service]
            a = max(a, s.age_min or a)
            b = min(b, s.age_max if s.age_max is not None else b)
            if a > b:
                a, b = self.p.bornes_classe_age(classe_age)
        p_retour = float(self._p_retour.get(classe_age, 0.0))
        connus = self.population[classe_age]
        if connus and rng.random() < p_retour:
            for _ in range(5):
                candidat = connus[int(rng.integers(len(connus)))]
                if not candidat.en_cours and candidat.sexe == sexe and a <= candidat.age <= b:
                    candidat.nb_venues += 1
                    return candidat
        self._n_patients += 1
        patient = Patient(id=f"P{self._n_patients:06d}", age=int(rng.integers(a, b + 1)), sexe=sexe, classe_age=classe_age, nb_venues=1)
        connus.append(patient)
        self.resultat.patients.append(patient)
        return patient

    def _ajuster_fin(self, duree_h: float, service: str) -> float:
        """Transforme une durée tirée au hasard en une durée qui se termine à une heure de sortie plausible.

        Les sorties ont lieu surtout en fin de matinée et sont moins nombreuses
        le week-end (sauf en soins critiques et UHCD).
        """
        if duree_h < 24.0:
            return duree_h
        rng = self.hasard["horaires"]
        maintenant = self.date()
        jours = max(1, int(round(duree_h / 24.0)))
        jour_sortie = (maintenant + timedelta(days=jours)).date()
        if service not in self._services_weekend and self._facteur_weekend < 1.0 and jour_sortie.weekday() >= 5:
            # Une partie des sorties du week-end est déplacée vers un jour de
            # semaine : moitié avancées au vendredi, moitié reportées au lundi,
            # pour ne pas allonger la durée moyenne de séjour (les durées
            # extraites des données réelles contiennent déjà cet effet).
            if rng.random() > self._facteur_weekend:
                vers_lundi = 7 - jour_sortie.weekday()           # +2 (samedi) ou +1 (dimanche)
                vers_vendredi = jour_sortie.weekday() - 4        # 1 (samedi) ou 2 (dimanche)
                if rng.random() < 0.5 and jours - vers_vendredi >= 1:
                    jour_sortie -= timedelta(days=vers_vendredi)
                else:
                    jour_sortie += timedelta(days=vers_lundi)
        fin = datetime.combine(jour_sortie, datetime.min.time()) + timedelta(hours=self.p.profil_sorties.tirer_heure(rng))
        return max(2.0, (fin - maintenant).total_seconds() / 3600.0)

    def _priorite_urgences(self, gravite: int) -> int:
        return int(self.p.regles["priorite_par_gravite"].get(gravite, 2))

    # ------------------------------------------------------------------
    # Générateurs de flux
    # ------------------------------------------------------------------
    def generateur_urgences(self):
        """Arrivées aux urgences : processus de Poisson à rythme variable (méthode d'amincissement)."""
        p, env = self.p, self.env
        rng = self.hasard["arrivees_urgences"]
        rng_profil = self.hasard["profils_urgences"]
        base = float(p.urgences["passages_par_an"]) / (365.25 * 24.0)
        h, j, m = p.profil_heure_urgences, p.profil_jour_urgences, p.profil_mois_urgences
        lambda_max = base * h.max() * j.max() * m.max()
        while True:
            # on tire des candidates au rythme maximal, puis on n'en garde qu'une partie
            while True:
                yield env.timeout(rng.exponential(1.0 / lambda_max))
                d = self.date()
                lam = base * h[d.hour] * j[d.weekday()] * m[d.month - 1]
                if rng.random() < lam / lambda_max:
                    break
            i = int(rng_profil.choice(len(self._profils_patients_urg), p=self._poids_patients_urg))
            classe_age, sexe = self._profils_patients_urg[i]
            patient = self._nouveau_patient(classe_age, sexe, self.hasard["population"])
            gravites, pg = p.gravite_urgences[classe_age]
            gravite = gravites[int(rng_profil.choice(len(gravites), p=pg))]
            env.process(self.processus_urgences(patient, gravite))

    def generateur_programme(self):
        """Admissions programmées : chaque jour, pour chaque service, on tire le nombre d'entrées et leurs heures."""
        p, env = self.p, self.env
        rng = self.hasard["entrees_programmees"]
        while True:
            jour = self.date().weekday()
            for service, par_semaine in p.entrees_programmees.items():
                n = int(rng.poisson(par_semaine * float(p.profil_jour_programme[jour])))
                for _ in range(n):
                    delai = p.profil_heure_programme.tirer_heure(rng)
                    env.process(self._arrivee_programmee(service, delai))
            yield env.timeout(24.0)

    def _arrivee_programmee(self, service: str, delai_h: float):
        yield self.env.timeout(delai_h)
        rng = self.hasard["entrees_programmees"]
        profils, pp = self.p.profil_patients_programme[service]
        classe_age, sexe = profils[int(rng.choice(len(profils), p=pp))]
        patient = self._nouveau_patient(classe_age, sexe, self.hasard["population"], service=service)
        if patient.en_cours or not self.p.services[service].accepte(patient.age, sexe):
            return
        patient.en_cours = True
        parcours = self.constructeur.construire("programme", classe_age, sexe, service)
        yield from self.hospitaliser(patient, parcours, passage=None, priorite=int(self.p.regles["priorite_programme"]))
        patient.en_cours = False

    # ------------------------------------------------------------------
    # Processus d'un patient
    # ------------------------------------------------------------------
    def processus_urgences(self, patient: Patient, gravite: int):
        """Passage aux urgences, puis éventuellement hospitalisation."""
        p, env = self.p, self.env
        rng = self.hasard["issues_urgences"]
        if patient.en_cours:
            return  # déjà à l'hôpital : on ignore cette arrivée
        patient.en_cours = True
        self._n_passages += 1
        passage = Passage(id=f"U{self._n_passages:07d}", patient=patient, arrivee=env.now, gravite=gravite)
        self.resultat.passages.append(passage)

        issues, pi = p.issues_urgences[(patient.classe_age, gravite)]
        issue = issues[int(rng.choice(len(issues), p=pi))]
        mu, sigma = p.durees_passage_urgences[issue]
        yield env.timeout(float(rng.lognormal(mu, sigma)))

        if issue != "hospitalisation":
            passage.sortie, passage.mode_sortie = env.now, issue
            patient.en_cours = False
            return

        premier = self._premier_service_compatible(patient)
        parcours = self.constructeur.construire("urgences", patient.classe_age, patient.sexe, premier)
        yield from self.hospitaliser(patient, parcours, passage=passage, priorite=self._priorite_urgences(gravite))
        patient.en_cours = False

    def _premier_service_compatible(self, patient: Patient) -> str:
        for _ in range(20):
            s = self.constructeur.premier_service(patient.classe_age, patient.sexe)
            if self.p.services[s].accepte(patient.age, patient.sexe):
                return s
        return "MED_POLY" if "MED_POLY" in self.p.services else self.p.services_avec_lits[0]

    def hospitaliser(self, patient: Patient, parcours: Parcours, passage: Passage | None, priorite: int):
        """Le séjour : enchaîne les étapes du parcours, en passant à chaque fois par le gestionnaire de lits."""
        p, env, lits = self.p, self.env, self.lits
        regles = p.regles
        self._n_sejours += 1
        sejour = Sejour(id=f"S{self._n_sejours:06d}", patient=patient, mode_entree="urgences" if passage else "programme",
                        id_passage=passage.id if passage else None, origine_parcours=parcours.origine,
                        trajectoire_prevue=parcours.trajectoire_prevue())
        self.resultat.sejours.append(sejour)
        if passage:
            passage.id_sejour = sejour.id

        service = parcours.premiere()
        precedent: str | None = None
        obtention: Obtention | None = None
        rum: Rum | None = None

        while True:
            if obtention is None:
                # Première demande de lit, depuis les urgences ou depuis le domicile.
                if passage:
                    obt = yield from lits.obtenir(service, patient.age, patient.sexe, priorite,
                                                  float(p.urgences["delai_max_attente_h"]), float(p.urgences["delai_transfert_h"]),
                                                  float(p.urgences.get("pas_reessai_h", 1.0)))
                else:
                    obt = yield from lits.obtenir(service, patient.age, patient.sexe, priorite,
                                                  float(regles["delai_max_attente_mutation_h"]), None)
                if obt is None:
                    # Aucun lit dans les délais : transfert vers un autre établissement.
                    passage.sortie, passage.mode_sortie, passage.transfert_faute_de_lit = env.now, "transfert", True
                    self.resultat.sejours.remove(sejour)
                    passage.id_sejour = None
                    return
                if passage:
                    passage.sortie, passage.mode_sortie, passage.destination, passage.attente_lit_h = env.now, "mutation", obt.service, obt.attente_h
                sejour.attente_entree_h = obt.attente_h
                sejour.entree = env.now
                mode_entree_rum = "urgences" if passage else "domicile"
                sejour.mouvements.append(Mouvement(env.now, "entree", "URG" if passage else None, None, obt.service, obt.lit))
            else:
                # Mutation : le patient garde son lit actuel pendant qu'il attend le suivant.
                obt = yield from lits.obtenir(service, patient.age, patient.sexe, int(regles["priorite_mutation"]),
                                              float(regles["delai_max_attente_mutation_h"]), None)
                sejour.attente_mutations_h += obt.attente_h
                lits.liberer(obtention)
                rum.sortie, rum.mode_sortie = env.now, "mutation"
                sejour.mouvements.append(Mouvement(env.now, "mutation", obtention.service, obtention.lit, obt.service, obt.lit))
                mode_entree_rum = "mutation"

            obtention = obt
            rum = Rum(rang=len(sejour.rums) + 1, unite=obt.service, specialite_prevue=service, heberge=obt.heberge,
                      lit=obt.lit, entree=env.now, mode_entree=mode_entree_rum)
            sejour.rums.append(rum)

            duree = self._ajuster_fin(self.constructeur.duree_heures(service, sejour.mode_entree, patient.classe_age), service)
            fin = env.now + duree

            if obt.heberge and obt.requete_origine is not None:
                # Hébergé : on attend soit la fin du séjour, soit un lit dans le service prévu.
                resultat = yield obt.requete_origine | env.timeout(fin - env.now)
                reste = fin - env.now
                if obt.requete_origine in resultat and reste >= float(regles["duree_min_restante_pour_retour_h"]):
                    lits.liberer(obt)
                    nouveau = lits.entrer_sur_requete(service, obt.requete_origine)
                    rum.sortie, rum.mode_sortie, rum.retour_origine = env.now, "mutation", True
                    sejour.mouvements.append(Mouvement(env.now, "mutation", obt.service, obt.lit, nouveau.service, nouveau.lit))
                    obtention = obt = nouveau
                    rum = Rum(rang=len(sejour.rums) + 1, unite=service, specialite_prevue=service, heberge=False,
                              lit=nouveau.lit, entree=env.now, mode_entree="mutation")
                    sejour.rums.append(rum)
                    if reste > 0:
                        yield env.timeout(reste)
                else:
                    lits.annuler(obt.requete_origine, service)
                    obt.requete_origine = None
                    if reste > 0:
                        yield env.timeout(reste)
            else:
                yield env.timeout(duree)

            suite = self.constructeur.suivante(parcours, service, precedent)
            if suite in SORTIES:
                lits.liberer(obtention)
                rum.sortie, rum.mode_sortie = env.now, suite
                sejour.sortie, sejour.mode_sortie = env.now, suite
                sejour.mouvements.append(Mouvement(env.now, "sortie", obtention.service, obtention.lit, None, None))
                return
            precedent, service = service, suite

    # ------------------------------------------------------------------
    def lancer(self) -> Resultat:
        self.env.process(self.generateur_urgences())
        self.env.process(self.generateur_programme())
        self.env.run(until=self.fin_simulation_h)
        return self.resultat


def simuler(p: Parametres, graine: int | None = None, duree_jours: int | None = None,
            chauffe_jours: int | None = None, marge_fin_jours: int | None = None) -> Resultat:
    """Lance une simulation complète et renvoie son résultat."""
    sim = p.simulation
    hopital = Hopital(
        p,
        graine=int(sim["graine"] if graine is None else graine),
        duree_jours=int(sim["duree_jours"] if duree_jours is None else duree_jours),
        chauffe_jours=int(sim.get("chauffe_jours", 0) if chauffe_jours is None else chauffe_jours),
        marge_fin_jours=int(sim.get("marge_fin_jours", 0) if marge_fin_jours is None else marge_fin_jours),
    )
    return hopital.lancer()
