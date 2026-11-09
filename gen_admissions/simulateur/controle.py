"""Le tableau de contrôle : les indicateurs recalculés sur les sorties, comparés aux cibles.

Les indicateurs sont ceux de ``cibles.csv`` (voir le dictionnaire) et ils sont
recalculés **sur les tables de sortie**, avec les mêmes définitions que l'extraction
sur les données réelles — un passage de spécialité est obtenu par
``passages_specialite`` du module de préparation, exactement comme dans le
notebook. Ainsi observé et simulé sont comparables terme à terme.

- par service : ``presents_moyens`` (somme des durées de passage ÷ jours), ``dms``
  (durée moyenne d'un passage), ``part_urgences`` (part des passages de séjours venus
  des urgences) ;
- pour l'hôpital : ``nb_etapes`` (passages par séjour), ``part_hospitalisation``,
  ``part_mono_uhcd``, ``part_transfert`` (parts des venues), ``part_rang2`` (part des
  hospitalisés entrés dans un service de rang 2), ``tension_moyenne``.

Le tableau rend, pour chaque indicateur : la cible (observé), le simulé, la fourchette
``[min, max]`` et si le simulé y est.
"""
from __future__ import annotations

import pandas as pd

from gen_admissions.extraction import passages_specialite

from .parametres import Parametres


def passages_simules(tabl: dict[str, pd.DataFrame], p: Parametres) -> pd.DataFrame:
    """Les passages de spécialité des séjours simulés, au format de ``passages_specialite`` (UHCD exclue)."""
    rum, rsa = tabl["rum"], tabl["rsa"]
    urg = p.structure.service_urgences.code
    r = rum[rum["spe_uma"] != urg].merge(rsa[["nas", "sim_origine", "sim_classe_age"]], on="nas", how="left")
    brut = pd.DataFrame({"id_sejour": r["nas"], "rang": r["norum"], "specialite": r["spe_uma"], "date_entree": r["d8eeue"], "date_sortie": r["d8soue"],
                         "mode_entree": r["sim_origine"], "classe_age": r["sim_classe_age"]})
    return passages_specialite(brut, contexte=[])


def indicateurs(tabl: dict[str, pd.DataFrame], p: Parametres, nb_jours: int) -> pd.DataFrame:
    """Les indicateurs simulés, une ligne par (indicateur, service)."""
    lignes = []
    passages = passages_simules(tabl, p)
    for spec, g in passages.groupby("specialite"):
        lignes.append({"indicateur": "presents_moyens", "service": spec, "simule": g["duree_j"].sum() / nb_jours})
        lignes.append({"indicateur": "dms", "service": spec, "simule": g["duree_j"].mean()})
        lignes.append({"indicateur": "part_urgences", "service": spec, "simule": g["mode_entree"].eq("urgences").mean()})
    if len(passages):
        lignes.append({"indicateur": "nb_etapes", "service": "", "simule": passages.groupby("id_sejour").size().mean()})
    rpu = tabl["rpu"]
    if len(rpu):
        parts = rpu["sim_situation"].value_counts(normalize=True)
        for sit in ("hospitalisation", "mono_uhcd", "transfert"):
            lignes.append({"indicateur": f"part_{sit}", "service": "", "simule": float(parts.get(sit, 0.0))})
        hosp = rpu[(rpu["sim_situation"] == "hospitalisation") & rpu["sim_rang"].notna()]
        if len(hosp):
            lignes.append({"indicateur": "part_rang2", "service": "", "simule": float((hosp["sim_rang"] == 2).mean())})
        lignes.append({"indicateur": "attente_lit_moyenne_h", "service": "", "simule": float(hosp["sim_attente_lit_h"].mean()) if len(hosp) else 0.0})
    if len(tabl["tension"]):
        lignes.append({"indicateur": "tension_moyenne", "service": "", "simule": float(tabl["tension"]["tension"].mean())})
    return pd.DataFrame(lignes)


def tableau_controle(tabl: dict[str, pd.DataFrame], p: Parametres, nb_jours: int) -> pd.DataFrame:
    """Cibles et simulé côte à côte : indicateur, service, observe, simule, min, max, dans_fourchette, ecart."""
    sim = indicateurs(tabl, p, nb_jours)
    cibles = p.cibles.copy()
    cibles["service"] = cibles["service"].fillna("").astype(str)
    t = cibles.merge(sim, on=["indicateur", "service"], how="outer")
    t["dans_fourchette"] = ((t["simule"] >= t["min"]) & (t["simule"] <= t["max"])).astype("boolean")
    t.loc[t["min"].isna() | t["simule"].isna(), "dans_fourchette"] = pd.NA
    t["ecart"] = t["simule"] - t["observe"]
    t["ecart_relatif"] = (t["ecart"] / t["observe"].where(t["observe"] != 0)).round(3)
    ordre = {"presents_moyens": 0, "dms": 1, "part_urgences": 2}
    t["_o"] = t["indicateur"].map(ordre).fillna(9)
    t = t.sort_values(["_o", "service", "indicateur"]).drop(columns="_o")
    return t[["indicateur", "service", "classe_tension", "observe", "simule", "min", "max", "dans_fourchette", "ecart", "ecart_relatif"]].reset_index(drop=True)


def bilan(tableau: pd.DataFrame) -> str:
    """Quelques lignes : combien d'indicateurs dans la fourchette, et les plus éloignés."""
    avec = tableau[tableau["dans_fourchette"].notna()]
    n_ok = int(avec["dans_fourchette"].sum())
    lignes = [f"{n_ok} indicateurs sur {len(avec)} dans leur fourchette"]
    hors = avec[~avec["dans_fourchette"].astype(bool)].copy()
    if len(hors):
        hors["abs"] = hors["ecart_relatif"].abs()
        lignes.append("les plus éloignés :")
        for _, r in hors.sort_values("abs", ascending=False).head(10).iterrows():
            lignes.append(f"  {r['indicateur']:<22s} {r['service']:<10s} observé {r['observe']:.3g}, simulé {r['simule']:.3g} ({r['ecart_relatif']:+.0%})")
    return "\n".join(lignes)
