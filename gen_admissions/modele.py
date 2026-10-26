"""Objets qui décrivent ce qui s'est passé pendant la simulation (le « journal »).

Ces objets sont remplis par le moteur au fil des événements, puis transformés
en tables (RPU, RSS, RUM, mouvements, vérité de référence) par ``export.py``.
Les instants sont en heures depuis le début de la simulation.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Patient:
    id: str
    age: int
    sexe: str
    classe_age: str
    en_cours: bool = False      # actuellement aux urgences ou hospitalisé
    nb_venues: int = 0


@dataclass
class Passage:
    """Un passage aux urgences (futur RPU)."""

    id: str
    patient: Patient
    arrivee: float
    gravite: int
    sortie: float | None = None
    mode_sortie: str | None = None          # domicile, mutation, transfert, deces, parti
    destination: str | None = None          # service d'hospitalisation
    attente_lit_h: float | None = None      # temps entre la demande de lit et son obtention
    transfert_faute_de_lit: bool = False
    id_sejour: str | None = None


@dataclass
class Rum:
    """Un passage dans une unité (futur RUM)."""

    rang: int
    unite: str                  # service où le patient est physiquement
    specialite_prevue: str      # service dont relève le patient
    heberge: bool
    lit: str
    entree: float
    mode_entree: str            # "domicile", "urgences" ou "mutation"
    sortie: float | None = None
    mode_sortie: str | None = None   # mutation, domicile, SSR, EHPAD, transfert, deces
    retour_origine: bool = False     # ce RUM se termine par un retour d'hébergement vers le service prévu


@dataclass
class Mouvement:
    instant: float
    type: str                   # entree, mutation, sortie
    unite_depart: str | None
    lit_depart: str | None
    unite_arrivee: str | None
    lit_arrivee: str | None


@dataclass
class Sejour:
    """Une hospitalisation (futur RSS)."""

    id: str
    patient: Patient
    mode_entree: str            # "urgences" ou "programme"
    id_passage: str | None
    origine_parcours: str       # "catalogue" ou "table"
    trajectoire_prevue: str
    entree: float | None = None
    sortie: float | None = None
    mode_sortie: str | None = None
    rums: list[Rum] = field(default_factory=list)
    mouvements: list[Mouvement] = field(default_factory=list)
    attente_entree_h: float = 0.0       # attente du premier lit (aux urgences ou avant une entrée programmée)
    attente_mutations_h: float = 0.0    # attentes cumulées lors des mutations

    @property
    def nb_hebergements(self) -> int:
        return sum(1 for r in self.rums if r.heberge)

    @property
    def trajectoire_realisee(self) -> str:
        return " > ".join(r.unite for r in self.rums)
