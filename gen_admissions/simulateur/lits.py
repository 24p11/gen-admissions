"""Les lits des services et l'attribution d'un lit (règles D de la spécification).

Deux niveaux :

- ``Lits`` : les lits d'un service. Une capacité (``lits_ouverts`` de la structure
  s'il existe, sinon ``lits``), des occupants, une file d'attente ordonnée par
  priorité puis par ancienneté (``simpy.PriorityResource``), des numéros de lit
  pour l'export, et des fermetures (un lit fermé est occupé par un « patient
  fictif » jusqu'à sa réouverture : c'est ce qui permettra les fermetures
  saisonnières sans changer la capacité de la ressource).
- ``ParcDeLits`` : tous les services de l'hôpital, et la règle D complète,
  ``obtenir_lit``, un processus SimPy qui part des services candidats d'un
  patient (avec leurs parts et leurs rangs) et rend soit un lit, soit un
  transfert (urgences) ou un report (programmé) quand l'attente dépasse le
  délai. Les règles, dans l'ordre :

  1. **rang 1, sans attendre** : le service prévu s'il a un lit libre, sinon un
     service de rang 1 avec un lit libre, tiré au prorata des parts ;
  2. **rang 2, après un supplément d'attente** : le patient attend un temps tiré
     dans ``supplement_rang2`` ; si un lit de rang 1 se libère entre-temps il le
     prend ; à l'échéance, un service de rang 2 avec un lit libre, au prorata ;
  3. **dernier recours** : il attend le premier lit libéré dans un service de
     rang 1 ou 2 ; au-delà du délai (``delai_transfert_h`` pour un patient des
     urgences, ``delai_report_programme_h`` pour un programmé), transfert ou report ;
  4. **soins critiques** : quand le service visé est un service de soins
     critiques, pas de redirection : le patient attend ce service, ou est
     transféré au-delà du délai.

Quand un lit se libère, il va à la demande de priorité la plus forte, puis à la
plus ancienne ; les priorités (sortie de soins critiques 0, urgences et mutations 1,
programmé 2) sont dans la structure. Le temps SimPy est en **heures**.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, Sequence

import numpy as np
import simpy

from .parametres import Parametres, Service

PRIORITES_DEFAUT = {"sortie_soins_critiques": 0, "urgences": 1, "mutation": 1, "programme": 2}
PRIORITE_FERMETURE = -10            # une fermeture passe avant toute demande de patient


@dataclass
class Occupation:
    """Un lit tenu par un patient (ou par une fermeture)."""
    requete: simpy.resources.resource.PriorityRequest
    lit: int
    patient: object | None            # None pour une fermeture
    depuis: float


class Lits:
    """Les lits d'un service."""

    def __init__(self, env: simpy.Environment, service: Service, capacite: int):
        self.env = env
        self.service = service
        self.capacite = int(capacite)
        self.ressource = simpy.PriorityResource(env, capacity=max(1, self.capacite))
        self._libres: list[int] = list(range(self.capacite, 0, -1))      # numéros de lit disponibles (pile)
        self._occupations: dict[simpy.resources.resource.PriorityRequest, Occupation] = {}
        self._fermetures: list[Occupation] = []
        self.total_entrees = 0

    # -- état ---------------------------------------------------------------

    @property
    def occupes(self) -> int:
        """Lits tenus par des patients (les fermetures ne comptent pas)."""
        return len(self._occupations) - len(self._fermetures)

    @property
    def fermes(self) -> int:
        return len(self._fermetures)

    @property
    def libres(self) -> int:
        """Lits libres pour un patient : capacité moins occupés, moins fermés, moins les demandes déjà servies."""
        return self.capacite - len(self.ressource.users) - len([q for q in self.ressource.queue if q.priority <= PRIORITE_FERMETURE])

    @property
    def en_attente(self) -> int:
        return len([q for q in self.ressource.queue if q.priority > PRIORITE_FERMETURE])

    def presents(self, filtre: Callable[[object], bool] | None = None) -> int:
        return sum(1 for o in self._occupations.values() if o.patient is not None and (filtre is None or filtre(o.patient)))

    def patients_presents(self) -> list[object]:
        return [o.patient for o in self._occupations.values() if o.patient is not None]

    # -- demandes -------------------------------------------------------------

    def demander(self, priorite: int) -> simpy.resources.resource.PriorityRequest:
        """Une demande de lit : un événement SimPy, réalisé quand un lit est attribué.
        Après réalisation, appeler ``installer`` ; une demande abandonnée se ``retirer``."""
        return self.ressource.request(priority=priorite)

    def installer(self, requete: simpy.resources.resource.PriorityRequest, patient: object) -> Occupation:
        """Le patient prend possession du lit obtenu par cette demande."""
        lit = self._libres.pop() if self._libres else 0
        occ = Occupation(requete=requete, lit=lit, patient=patient, depuis=self.env.now)
        self._occupations[requete] = occ
        self.total_entrees += 1
        return occ

    def liberer(self, occupation: Occupation) -> None:
        self._occupations.pop(occupation.requete, None)
        if occupation.lit:
            self._libres.append(occupation.lit)
        self.ressource.release(occupation.requete)

    def retirer(self, requete: simpy.resources.resource.PriorityRequest) -> None:
        """Abandonne une demande : retirée de la file si elle attend, rendue si elle venait d'être servie."""
        if requete.triggered:
            self.ressource.release(requete)
        else:
            requete.cancel()

    # -- fermetures -------------------------------------------------------------

    def fermer(self, n: int = 1):
        """Ferme ``n`` lits : chacun est pris par une fermeture dès qu'il se libère (processus SimPy)."""
        for _ in range(n):
            req = self.ressource.request(priority=PRIORITE_FERMETURE)
            yield req
            lit = self._libres.pop() if self._libres else 0
            occ = Occupation(requete=req, lit=lit, patient=None, depuis=self.env.now)
            self._occupations[req] = occ
            self._fermetures.append(occ)

    def rouvrir(self, n: int = 1) -> int:
        """Rouvre jusqu'à ``n`` lits fermés ; renvoie le nombre rouvert."""
        rouverts = 0
        while self._fermetures and rouverts < n:
            self.liberer(self._fermetures.pop())
            rouverts += 1
        return rouverts


@dataclass
class ResultatLit:
    """Ce que rend ``obtenir_lit`` : un lit (service, occupation, rang) ou une issue sans lit."""
    issue: str                        # "lit", "transfert", "report"
    service: str = ""
    occupation: Occupation | None = None
    rang: int | None = None
    attente_h: float = 0.0
    supplement_h: float = 0.0
    regle: str = ""                   # "prevu", "rang1", "rang2", "dernier_recours", "soins_critiques"


class ParcDeLits:
    """Tous les lits de l'hôpital, et la règle D."""

    def __init__(self, env: simpy.Environment, p: Parametres, rng: np.random.Generator, capacites: dict[str, int] | None = None):
        self.env, self.p, self.rng = env, p, rng
        self.priorites = {**PRIORITES_DEFAUT, **(p.structure.lits.get("priorites") or {})}
        self.delai_transfert_h = float(p.structure.urgences.get("delai_transfert_h", 24))
        self.delai_report_h = float(p.structure.lits.get("delai_report_programme_h", 8))
        self.lits: dict[str, Lits] = {}
        for code, s in p.structure.services.items():
            capacite = (capacites or {}).get(code, capacite_de(s))
            self.lits[code] = Lits(env, s, capacite)

    # -- comptages ------------------------------------------------------------

    def presents(self, filtre: Callable[[object], bool] | None = None, services: Iterable[str] | None = None) -> int:
        codes = list(services) if services is not None else list(self.lits)
        return sum(self.lits[c].presents(filtre) for c in codes)

    def etat(self) -> dict[str, tuple[int, int, int]]:
        """Par service : (occupés, libres, en attente)."""
        return {c: (l.occupes, l.libres, l.en_attente) for c, l in self.lits.items()}

    # -- la règle D -------------------------------------------------------------

    def obtenir_lit(self, patient, candidats: Sequence[tuple[str, float, int]], service_prevu: str, origine: str,
                    classe_tension: str, supplement: Callable[[str], float] | None = None, delai_max_h: float | None = None):
        """Processus SimPy : ``resultat = yield env.process(parc.obtenir_lit(...))``.

        ``candidats`` : (service, part, rang) déjà restreints à la structure et renormalisés ;
        ``origine`` : « urgences », « programme » ou « mutation » (priorité et délai) ;
        ``supplement`` : fonction classe_tension → heures (D4), par défaut celle des paramètres ;
        ``delai_max_h`` : délai avant transfert / report, par défaut celui de la structure.
        """
        env = self.env
        debut = env.now
        priorite = self.priorites.get(origine, self.priorites["urgences"])
        issue_sans_lit = "report" if origine == "programme" else "transfert"
        if delai_max_h is None:
            delai_max_h = self.delai_report_h if origine == "programme" else self.delai_transfert_h
        candidats = [(s, part, rang) for s, part, rang in candidats if s in self.lits]
        if not candidats:
            return ResultatLit(issue=issue_sans_lit, attente_h=0.0, regle="aucun_service")

        # 4. soins critiques : pas de redirection, on attend le service visé (ou transfert / report)
        vise = self.lits.get(service_prevu)
        if vise is not None and vise.service.soins_critiques:
            resultat = yield env.process(self._attendre([service_prevu], priorite, delai_max_h, patient))
            resultat.rang = 1
            resultat.regle = "soins_critiques"
            resultat.attente_h = env.now - debut
            if resultat.issue != "lit":
                resultat.issue = issue_sans_lit
            return resultat

        rang1 = [(s, part) for s, part, rang in candidats if rang == 1]
        rang2 = [(s, part) for s, part, rang in candidats if rang == 2]

        # 1. rang 1, sans attendre
        if service_prevu in self.lits and self.lits[service_prevu].libres > 0:
            occ = yield from self._prendre(service_prevu, priorite, patient)
            rang_prevu = next((r for s, _, r in candidats if s == service_prevu), 1)
            return ResultatLit("lit", service_prevu, occ, rang_prevu, env.now - debut, regle="prevu")
        choix = self._tirer_libre(rang1)
        if choix:
            occ = yield from self._prendre(choix, priorite, patient)
            return ResultatLit("lit", choix, occ, 1, env.now - debut, regle="rang1")

        # 2. rang 2, après un supplément d'attente (un lit de rang 1 libéré entre-temps est pris)
        supplement_h = 0.0
        if rang2 and rang1:
            supplement_h = float((supplement or self.p_supplement)(classe_tension))
            supplement_h = min(supplement_h, delai_max_h)
            resultat = yield env.process(self._attendre([s for s, _ in rang1], priorite, supplement_h, patient))
            if resultat.issue == "lit":
                resultat.rang, resultat.regle, resultat.attente_h, resultat.supplement_h = 1, "rang1", env.now - debut, supplement_h
                return resultat
        if rang2:
            choix = self._tirer_libre(rang2)
            if choix:
                occ = yield from self._prendre(choix, priorite, patient)
                return ResultatLit("lit", choix, occ, 2, env.now - debut, supplement_h, regle="rang2")

        # 3. dernier recours : le premier lit libéré parmi les rangs 1 et 2, dans le délai restant
        restant = max(0.0, delai_max_h - (env.now - debut))
        services = [s for s, _ in rang1] + [s for s, _ in rang2]
        resultat = yield env.process(self._attendre(services, priorite, restant, patient))
        resultat.attente_h = env.now - debut
        resultat.supplement_h = supplement_h
        if resultat.issue == "lit":
            resultat.rang = 1 if resultat.service in dict(rang1) else 2
            resultat.regle = "dernier_recours"
        else:
            resultat.issue = issue_sans_lit
            resultat.regle = "delai_depasse"
        return resultat

    # -- mécanique ---------------------------------------------------------------

    def p_supplement(self, classe_tension: str) -> float:
        """Le supplément de rang 2 des paramètres, en heures (tranche → uniforme dans la tranche)."""
        from .tirages import TRANCHES_H
        tranche = self.p.supplement.tirer(self.rng, classe_tension=classe_tension)
        bas, haut = TRANCHES_H[tranche]
        return 24 + float(self.rng.exponential(12.0)) if haut is None else bas + float(self.rng.random()) * (haut - bas)

    def _tirer_libre(self, services: Sequence[tuple[str, float]]) -> str | None:
        libres = [(s, part) for s, part in services if self.lits[s].libres > 0]
        if not libres:
            return None
        parts = np.array([p for _, p in libres], dtype=float)
        return libres[int(self.rng.choice(len(libres), p=parts / parts.sum()))][0]

    def _prendre(self, service: str, priorite: int, patient):
        """Prend un lit libre dans ``service`` (la demande est servie immédiatement)."""
        req = self.lits[service].demander(priorite)
        yield req
        return self.lits[service].installer(req, patient)

    def _attendre(self, services: Sequence[str], priorite: int, delai_h: float, patient):
        """Attend le premier lit libéré parmi ``services``, au plus ``delai_h`` heures.
        Les demandes non servies sont retirées ; si deux sont servies en même temps, une est rendue."""
        env = self.env
        demandes = {s: self.lits[s].demander(priorite) for s in services}
        delai = env.timeout(max(0.0, delai_h))
        yield simpy.AnyOf(env, [*demandes.values(), delai])
        servies = [s for s, d in demandes.items() if d.triggered and not isinstance(d.value, Exception)]
        choix = None
        if servies:
            # plusieurs lits libérés au même instant : le service le plus tôt dans la liste (rang 1 avant rang 2)
            choix = servies[0]
        for s, d in demandes.items():
            if s != choix:
                self.lits[s].retirer(d)
        if choix is None:
            return ResultatLit(issue="attente_depassee")
        occ = self.lits[choix].installer(demandes[choix], patient)
        return ResultatLit("lit", choix, occ)


def capacite_de(service: Service) -> int:
    """La capacité qu'utilise le simulateur : ``lits_ouverts`` s'il est renseigné, sinon ``lits``."""
    return service.capacite
