"""Construction du parcours d'un patient hospitalisé : catalogue, table de passage, durées.

Deux méthodes complémentaires (voir le plan d'analyse, section 2.3) :

- le **catalogue de trajectoires** donne des parcours complets, exacts de
  bout en bout, pour les trajectoires fréquentes ;
- la **table de passage** construit le parcours étape par étape, pour les
  séjours que le catalogue ne couvre pas.

Dans les deux cas, le parcours décrit ce que la médecine « prévoit ». Les
attentes et les hébergements sont ajoutés ensuite par le gestionnaire de lits.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .config import Parametres, SORTIES
from .extraction import AUCUN_PRECEDENT


@dataclass
class Parcours:
    """Le parcours prévu d'un séjour.

    - ``origine`` : « catalogue » ou « table » ;
    - ``etapes`` : pour le catalogue, la liste complète des services ; pour la
      table, elle se remplit au fur et à mesure ;
    - ``mode_sortie`` : connu d'avance pour le catalogue, décidé à la fin pour la table.
    """

    origine: str
    mode_entree: str
    classe_age: str
    etapes: list[str] = field(default_factory=list)
    mode_sortie: str | None = None
    _index: int = 0

    def premiere(self) -> str:
        self._index = 1
        return self.etapes[0]

    def trajectoire_prevue(self) -> str:
        return " > ".join(self.etapes)


class ConstructeurParcours:
    """Tire les parcours et les durées à partir des paramètres."""

    def __init__(self, p: Parametres, rng_parcours: np.random.Generator, rng_durees: np.random.Generator) -> None:
        self.p = p
        self.rng = rng_parcours
        self.rng_durees = rng_durees
        self.seuil = p.seuil
        # Index du catalogue par (mode_entree, classe_age, sexe, premier service) pour un tirage rapide.
        self._catalogue: dict[tuple[str, str, str, str], tuple[list[list[str]], list[str], np.ndarray, int]] = {}
        for cle, grp in p.catalogue.groupby(["mode_entree", "classe_age", "sexe", "premier_service"]):
            eff = grp["effectif"].to_numpy(dtype=float)
            self._catalogue[cle] = (list(grp["etapes"]), list(grp["mode_sortie"]), eff / eff.sum(), int(eff.sum()))
        self.max_etapes = 8

    # ---- premier service ------------------------------------------------
    def premier_service(self, classe_age: str, sexe: str) -> str:
        """Premier service d'un patient venu des urgences (selon âge et sexe, avec repli)."""
        for cle in ((("urgences", classe_age, sexe)), ("urgences", classe_age, "M"), ("urgences", classe_age, "F")):
            if cle in self.p.premier_service:
                services, probas, _ = self.p.premier_service[cle]
                return services[int(self.rng.choice(len(services), p=probas))]
        raise KeyError(f"premier_service : aucune répartition pour {classe_age}")

    # ---- choix catalogue / table ----------------------------------------------
    def construire(self, mode_entree: str, classe_age: str, sexe: str, premier: str) -> Parcours:
        """Décide si le séjour suit une trajectoire du catalogue ou la table de passage."""
        cle_cat = (mode_entree, classe_age, sexe, premier)
        cle_prem = (mode_entree, classe_age, sexe)
        if cle_cat in self._catalogue and cle_prem in self.p.premier_service:
            etapes_list, sorties, probas, eff_cat = self._catalogue[cle_cat]
            services, probas_prem, eff_total = self.p.premier_service[cle_prem]
            # effectif total des séjours de ce profil commençant par ce service
            if premier in services:
                eff_prem = eff_total * float(probas_prem[services.index(premier)])
                couverture = min(1.0, eff_cat / max(eff_prem, 1.0))
                if self.rng.random() < couverture:
                    i = int(self.rng.choice(len(etapes_list), p=probas))
                    return Parcours("catalogue", mode_entree, classe_age, list(etapes_list[i]), sorties[i])
        return Parcours("table", mode_entree, classe_age, [premier], None)

    # ---- table de passage avec repli ---------------------------------------
    def suivante(self, parcours: Parcours, service: str, precedent: str | None) -> str:
        """Après le passage dans ``service``, où va le patient ? (service suivant ou mode de sortie)."""
        if parcours.origine == "catalogue":
            if parcours._index < len(parcours.etapes):
                suite = parcours.etapes[parcours._index]
                parcours._index += 1
                return suite
            return parcours.mode_sortie
        # table de passage : on cherche la case la plus détaillée assez remplie
        if len(parcours.etapes) >= self.max_etapes:
            parcours.mode_sortie = "domicile"
            return "domicile"
        prec = precedent or AUCUN_PRECEDENT
        niveaux = [("complet", (service, prec, parcours.classe_age, parcours.mode_entree)),
                   ("sans_precedent", (service, parcours.classe_age, parcours.mode_entree)),
                   ("sans_age", (service, parcours.mode_entree)),
                   ("service", (service,))]
        for niveau, cle in niveaux:
            case = self.p.table_passage[niveau].get(cle)
            if case is not None and case[2] >= self.seuil:
                suites, probas, _ = case
                suite = suites[int(self.rng.choice(len(suites), p=probas))]
                if suite in SORTIES:
                    parcours.mode_sortie = suite
                else:
                    parcours.etapes.append(suite)
                return suite
        raise KeyError(f"table_passage : aucune case disponible pour le service {service}")

    # ---- durées ------------------------------------------------------------------
    def duree_heures(self, service: str, mode_entree: str, classe_age: str) -> float:
        """Durée de passage dans une unité (heures), avec repli sur des tables plus générales."""
        d = self.p.durees_sejour
        loi = d["complet"].get((service, mode_entree, classe_age)) or d["sans_age"].get((service, mode_entree)) or d["service"][(service,)]
        return loi.tirer_heures(self.rng_durees)
