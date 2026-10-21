"""Requêtes SQLite et opérations métier, sans dépendance à Flask."""

import json
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash

from models import Bouteille, Cave, Etagere, Utilisateur, Vin


# Vérifier aussi un hash pour un identifiant inconnu évite une réponse immédiate
# qui permettrait de distinguer facilement les comptes existants.
HASH_FACTICE = generate_password_hash("mot-de-passe-factice-inutilisable")


@contextmanager
def connexion(chemin):
    base = sqlite3.connect(chemin)
    base.row_factory = sqlite3.Row
    base.execute("PRAGMA foreign_keys = ON")
    try:
        yield base
        base.commit()
    except Exception:
        base.rollback()
        raise
    finally:
        base.close()


def initialiser(chemin):
    # Sauvegarde SQLite avant la migration de la première version, sans comptes.
    # backup() copie aussi les données éventuellement présentes dans le journal.
    with connexion(chemin) as base:
        colonnes = {ligne["name"] for ligne in base.execute("PRAGMA table_info(etageres)")}
        ancienne_version = bool(colonnes) and "cave_id" not in colonnes
        if ancienne_version:
            sauvegarde = Path(chemin).with_name(
                "cavissimo-avant-comptes-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".sqlite3"
            )
            with closing(sqlite3.connect(sauvegarde)) as copie:
                base.backup(copie)

    with connexion(chemin) as base:
        # Seule cette migration désactive les clés étrangères, avant d'ouvrir
        # la transaction, pour reconstruire la contrainte d'unicité des étagères.
        base.execute("PRAGMA foreign_keys = OFF")
        base.execute("BEGIN IMMEDIATE")
        base.execute("""
            CREATE TABLE IF NOT EXISTS utilisateurs (
                id INTEGER PRIMARY KEY,
                identifiant TEXT NOT NULL,
                identifiant_cle TEXT NOT NULL UNIQUE,
                email TEXT NOT NULL,
                mot_de_passe_hash TEXT NOT NULL
            )
        """)
        base.execute("""
            CREATE TABLE IF NOT EXISTS caves (
                id INTEGER PRIMARY KEY,
                utilisateur_id INTEGER NOT NULL UNIQUE REFERENCES utilisateurs(id)
            )
        """)
        if ancienne_version:
            base.execute("""
                CREATE TABLE etageres_comptes (
                    id INTEGER PRIMARY KEY,
                    cave_id INTEGER REFERENCES caves(id),
                    nom TEXT NOT NULL,
                    nom_cle TEXT NOT NULL,
                    capacite_max INTEGER NOT NULL CHECK(capacite_max > 0),
                    UNIQUE(cave_id, nom_cle)
                )
            """)
            base.execute("""
                INSERT INTO etageres_comptes (id, nom, nom_cle, capacite_max)
                SELECT id, nom, nom_cle, capacite_max FROM etageres
            """)
            base.execute("DROP TABLE etageres")
            base.execute("ALTER TABLE etageres_comptes RENAME TO etageres")
        base.execute("""
            CREATE TABLE IF NOT EXISTS etageres (
                id INTEGER PRIMARY KEY,
                cave_id INTEGER REFERENCES caves(id),
                nom TEXT NOT NULL,
                nom_cle TEXT NOT NULL,
                capacite_max INTEGER NOT NULL CHECK(capacite_max > 0),
                UNIQUE(cave_id, nom_cle)
            )
        """)
        base.execute("""
            CREATE TABLE IF NOT EXISTS vins (
                id INTEGER PRIMARY KEY,
                domaine TEXT NOT NULL,
                nom_cuvee TEXT NOT NULL,
                type TEXT NOT NULL CHECK(type IN ('rouge', 'blanc', 'rosé', 'effervescent')),
                millesime INTEGER NOT NULL CHECK(millesime > 0),
                region TEXT NOT NULL,
                identite TEXT NOT NULL UNIQUE
            )
        """)
        base.execute("""
            CREATE TABLE IF NOT EXISTS bouteilles (
                id INTEGER PRIMARY KEY,
                vin_id INTEGER NOT NULL REFERENCES vins(id),
                etagere_id INTEGER NOT NULL REFERENCES etageres(id),
                prix_centimes INTEGER NOT NULL CHECK(prix_centimes >= 0),
                date_ajout TEXT NOT NULL
            )
        """)
        if base.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise RuntimeError("La migration a été annulée : relations SQLite invalides.")
        base.execute("PRAGMA user_version = 2")


def normaliser(texte):
    """Ignore la casse et les espaces répétés pour identifier les doublons."""
    return " ".join(texte.split()).casefold()


def utilisateur_depuis_ligne(ligne):
    if ligne is None:
        return None
    return Utilisateur(ligne["identifiant"], ligne["email"], ligne["mot_de_passe_hash"], ligne["id"])


def charger_utilisateur(chemin, utilisateur_id):
    with connexion(chemin) as base:
        ligne = base.execute("SELECT * FROM utilisateurs WHERE id = ?", (utilisateur_id,)).fetchone()
    return utilisateur_depuis_ligne(ligne)


def inscrire(chemin, identifiant, email, mot_de_passe):
    utilisateur = Utilisateur.creer(identifiant, email, mot_de_passe)
    try:
        with connexion(chemin) as base:
            base.execute("BEGIN IMMEDIATE")
            premier_compte = base.execute("SELECT COUNT(*) FROM utilisateurs").fetchone()[0] == 0
            resultat = base.execute("""
                INSERT INTO utilisateurs (identifiant, identifiant_cle, email, mot_de_passe_hash)
                VALUES (?, ?, ?, ?)
            """, (utilisateur.identifiant, normaliser(utilisateur.identifiant),
                  utilisateur.email, utilisateur.mot_de_passe_hash))
            utilisateur.id = resultat.lastrowid
            cave_id = base.execute("INSERT INTO caves (utilisateur_id) VALUES (?)", (utilisateur.id,)).lastrowid
            if premier_compte:
                # Les anciennes étagères, et donc leurs bouteilles, sont conservées.
                base.execute("UPDATE etageres SET cave_id = ? WHERE cave_id IS NULL", (cave_id,))
    except sqlite3.IntegrityError:
        raise ValueError("Cet identifiant est déjà utilisé.") from None
    return utilisateur


def authentifier(chemin, identifiant, mot_de_passe):
    with connexion(chemin) as base:
        ligne = base.execute("SELECT * FROM utilisateurs WHERE identifiant_cle = ?", (normaliser(identifiant),)).fetchone()
    utilisateur = utilisateur_depuis_ligne(ligne)
    if not 1 <= len(mot_de_passe) <= 128:
        return None
    if utilisateur is None:
        check_password_hash(HASH_FACTICE, mot_de_passe)
        return None
    return utilisateur if utilisateur.verifier_mot_de_passe(mot_de_passe) else None


def obtenir_cave(base, utilisateur_id):
    ligne = base.execute("SELECT * FROM caves WHERE utilisateur_id = ?", (utilisateur_id,)).fetchone()
    if ligne is None:
        raise PermissionError("Cave inaccessible.")
    cave = Cave(ligne["id"], ligne["utilisateur_id"])
    cave.verifier_proprietaire(utilisateur_id)
    return cave


def creer_etagere(chemin, utilisateur_id, nom, capacite):
    etagere = Etagere(nom, capacite)
    try:
        with connexion(chemin) as base:
            base.execute("BEGIN IMMEDIATE")
            cave = obtenir_cave(base, utilisateur_id)
            nombre = base.execute("SELECT COUNT(*) FROM etageres WHERE cave_id = ?", (cave.id,)).fetchone()[0]
            cave.verifier_ajout_etagere(nombre)
            base.execute(
                "INSERT INTO etageres (cave_id, nom, nom_cle, capacite_max) VALUES (?, ?, ?, ?)",
                (cave.id, etagere.nom, normaliser(etagere.nom), etagere.capacite_max),
            )
    except sqlite3.IntegrityError:
        raise ValueError("Une étagère porte déjà ce nom.") from None


def creer_vin(chemin, domaine, nom_cuvee, type_vin, millesime, region):
    vin = Vin(domaine, nom_cuvee, type_vin, millesime, region)
    identite = json.dumps([
        normaliser(vin.domaine), normaliser(vin.nom_cuvee), vin.type,
        vin.millesime, normaliser(vin.region),
    ], ensure_ascii=False)
    with connexion(chemin) as base:
        # Une référence existante est réutilisée, sans écraser ses informations.
        resultat = base.execute(
            """INSERT INTO vins (domaine, nom_cuvee, type, millesime, region, identite)
               VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(identite) DO NOTHING""",
            (vin.domaine, vin.nom_cuvee, vin.type, vin.millesime, vin.region, identite),
        )
        return resultat.rowcount == 1


def ajouter_bouteilles(chemin, utilisateur_id, etagere_id, vin_id, quantite, prix_centimes):
    Etagere._verifier_entier_positif(quantite)
    if (not isinstance(prix_centimes, int) or isinstance(prix_centimes, bool)
            or prix_centimes < 0):
        raise ValueError("Le prix doit être positif ou nul, exprimé en centimes.")

    with connexion(chemin) as base:
        # Le verrou couvre la lecture du stock ET les insertions : deux requêtes
        # simultanées ne peuvent pas réserver la même place.
        base.execute("BEGIN IMMEDIATE")
        cave = obtenir_cave(base, utilisateur_id)
        ligne_etagere = base.execute(
            "SELECT * FROM etageres WHERE id = ? AND cave_id = ?", (etagere_id, cave.id),
        ).fetchone()
        if ligne_etagere is None:
            # Même refus pour un identifiant inexistant et pour l'étagère d'autrui.
            raise PermissionError("Étagère inaccessible.")
        ligne_vin = base.execute("SELECT * FROM vins WHERE id = ?", (vin_id,)).fetchone()
        if ligne_etagere is None or ligne_vin is None:
            raise ValueError("Sélectionne une étagère et un vin existants.")

        etagere = Etagere(ligne_etagere["nom"], ligne_etagere["capacite_max"])
        vin = Vin(ligne_vin["domaine"], ligne_vin["nom_cuvee"], ligne_vin["type"],
                  ligne_vin["millesime"], ligne_vin["region"])

        # Reconstitue les exemplaires déjà présents pour appliquer les règles
        # de la classe Etagere, comme dans la démonstration en mémoire.
        stock = base.execute("""
            SELECT b.prix_centimes, v.* FROM bouteilles b
            JOIN vins v ON v.id = b.vin_id WHERE b.etagere_id = ?
        """, (etagere_id,)).fetchall()
        for ligne in stock:
            vin_stocke = Vin(ligne["domaine"], ligne["nom_cuvee"], ligne["type"],
                             ligne["millesime"], ligne["region"])
            etagere.ajouter_bouteille(Bouteille(vin_stocke, ligne["prix_centimes"] / 100))

        if not etagere.peut_accueillir(quantite):
            places = etagere.capacite_max - etagere.occupation()
            raise ValueError(f"Pas assez de place : {places} place(s) disponible(s). Aucun ajout effectué.")

        for _ in range(quantite):
            bouteille = Bouteille(vin, prix_centimes / 100)
            etagere.ajouter_bouteille(bouteille)
            base.execute("""
                INSERT INTO bouteilles (vin_id, etagere_id, prix_centimes, date_ajout)
                VALUES (?, ?, ?, ?)
            """, (vin_id, etagere_id, prix_centimes, bouteille.date_ajout.isoformat()))


def consulter_cave(chemin, utilisateur_id):
    with connexion(chemin) as base:
        cave = obtenir_cave(base, utilisateur_id)
        etageres = base.execute("""
            SELECT e.*, COUNT(b.id) AS occupation FROM etageres e
            LEFT JOIN bouteilles b ON b.etagere_id = e.id
            WHERE e.cave_id = ? GROUP BY e.id ORDER BY e.id
        """, (cave.id,)).fetchall()
        vins = base.execute("SELECT * FROM vins ORDER BY domaine, nom_cuvee, millesime").fetchall()
        bouteilles = base.execute("""
            SELECT b.*, v.domaine, v.nom_cuvee, v.type, v.millesime, e.nom AS etagere
            FROM bouteilles b JOIN vins v ON v.id = b.vin_id
            JOIN etageres e ON e.id = b.etagere_id WHERE e.cave_id = ? ORDER BY b.id DESC
        """, (cave.id,)).fetchall()
    capacite = sum(e["capacite_max"] for e in etageres)
    stats = {
        "bouteilles": len(bouteilles), "references": len({b["vin_id"] for b in bouteilles}),
        "etageres": len(etageres), "capacite": capacite,
        "occupation": round(len(bouteilles) / capacite * 100) if capacite else None,
        "valeur_centimes": sum(b["prix_centimes"] for b in bouteilles),
    }
    return {
        "etageres": etageres, "vins": vins, "bouteilles": bouteilles, "stats": stats,
        "max_etageres": Cave.MAX_ETAGERES,
        # Conserve aussi l'affichage des anciennes caves qui auraient plus de 5 étagères.
        "emplacements_etageres": max(Cave.MAX_ETAGERES, len(etageres)),
    }
