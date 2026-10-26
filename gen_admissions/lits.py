"""Gestionnaire de lits : attribution, attente, hébergement, transfert.

Chaque service avec des lits est une ``PriorityResource`` SimPy (un guichet à
N jetons, où les demandes prioritaires passent devant). Par-dessus, ce module
écrit les règles de la section 3.5 du plan :

1. s'il y a un lit libre dans le service demandé, le patient y entre ;
2. sinon, il attend, avec une priorité selon sa gravité et son mode d'entrée ;
3. si l'attente dépasse un délai fixé, il est hébergé dans un service
   compatible (sa demande dans le service d'origine reste en attente, pour
   pouvoir y revenir) ;
4. si rien ne se libère avant un délai plus long, le patient des urgences est
   transféré vers un autre établissement.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np
import simpy

from .config import Parametres


@dataclass
class Obtention:
    """Résultat d'une demande de lit."""

    service: str                  # service où le patient est physiquement
    requete: simpy.resources.resource.Request
    lit: str                      # numéro du lit, par exemple CAR-07
    heberge: bool
    requete_origine: simpy.resources.resource.Request | None  # demande toujours en attente dans le service prévu
    attente_h: float


class GestionnaireLits:
    def __init__(self, env: simpy.Environment, p: Parametres, rng: np.random.Generator) -> None:
        self.env = env
        self.p = p
        self.rng = rng
        self.ressources: dict[str, simpy.PriorityResource] = {}
        self.lits_libres: dict[str, deque[str]] = {}
        self.occupation: list[tuple[float, str, int]] = []  # (instant, service, +1 ou -1)
        for code in p.services_avec_lits:
            n = p.services[code].lits
            self.ressources[code] = simpy.PriorityResource(env, capacity=n)
            prefixe = code[:3]
            self.lits_libres[code] = deque(f"{prefixe}-{i:02d}" for i in range(1, n + 1))

    # ---- état ---------------------------------------------------------------
    def occupes(self, service: str) -> int:
        return self.ressources[service].count

    def lit_libre(self, service: str) -> bool:
        r = self.ressources[service]
        return r.count < r.capacity and len(r.queue) == 0

    def _service_compatible_libre(self, cible: str, age: int, sexe: str) -> str | None:
        """Premier service d'hébergement compatible qui a un lit libre (ordre de la liste, puis hasard)."""
        candidats = [s for s in self.p.services[cible].hebergement
                     if self.lit_libre(s) and self.p.services[s].accepte(age, sexe)]
        if not candidats:
            return None
        return candidats[0] if len(candidats) == 1 else candidats[int(self.rng.integers(len(candidats)))]

    # ---- entrée / sortie -------------------------------------------------------
    def _entrer(self, service: str, requete, heberge: bool, requete_origine, attente_h: float) -> Obtention:
        lit = self.lits_libres[service].popleft()
        self.occupation.append((self.env.now, service, +1))
        return Obtention(service, requete, lit, heberge, requete_origine, attente_h)

    def entrer_sur_requete(self, service: str, requete) -> Obtention:
        """Entre dans ``service`` avec une demande déjà accordée (retour d'hébergement)."""
        return self._entrer(service, requete, False, None, 0.0)

    def liberer(self, obtention: Obtention) -> None:
        """Rend le lit au service."""
        self.ressources[obtention.service].release(obtention.requete)
        self.lits_libres[obtention.service].append(obtention.lit)
        self.occupation.append((self.env.now, obtention.service, -1))

    def annuler(self, requete, service: str) -> None:
        """Abandonne une demande encore en attente (ou rend le lit si elle vient d'être accordée)."""
        if requete.triggered:
            self.ressources[service].release(requete)
        else:
            requete.cancel()

    # ---- la demande de lit ------------------------------------------------------
    def obtenir(self, cible: str, age: int, sexe: str, priorite: int, delai_hebergement_h: float,
                delai_transfert_h: float | None, pas_reessai_h: float = 1.0):
        """Processus SimPy : demande un lit dans ``cible`` et applique les règles d'attente.

        Renvoie une ``Obtention``, ou ``None`` si le patient doit être transféré
        (uniquement quand ``delai_transfert_h`` est fourni).
        """
        env = self.env
        t0 = env.now
        requete = self.ressources[cible].request(priority=priorite)

        # 1. On attend un lit dans le service prévu, mais pas plus de delai_hebergement_h.
        resultat = yield requete | env.timeout(delai_hebergement_h)
        if requete in resultat:
            return self._entrer(cible, requete, False, None, env.now - t0)

        # 2. Au-delà, à chaque pas, on cherche un hébergement ; sinon on continue d'attendre.
        while True:
            if requete.triggered:
                return self._entrer(cible, requete, False, None, env.now - t0)
            autre = self._service_compatible_libre(cible, age, sexe)
            if autre is not None:
                requete_heb = self.ressources[autre].request(priority=0)
                resultat = yield requete_heb | env.timeout(0.01)
                if requete_heb in resultat:
                    garder = bool(self.p.regles.get("retour_service_origine", True))
                    if not garder:
                        requete.cancel()
                    return self._entrer(autre, requete_heb, True, requete if garder else None, env.now - t0)
                requete_heb.cancel()
            if delai_transfert_h is not None and env.now - t0 >= delai_transfert_h:
                requete.cancel()
                return None
            resultat = yield requete | env.timeout(pas_reessai_h)
            if requete in resultat:
                return self._entrer(cible, requete, False, None, env.now - t0)
