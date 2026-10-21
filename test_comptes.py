"""Tests de connexion, d'isolation des caves et de migration de l'ancien stock."""

import tempfile
import unittest
from pathlib import Path

import database
from test_web import ScenarioWeb


class TestComptes(ScenarioWeb):
    def test_inscription_hash_et_cave_unique(self):
        self.assertEqual(self.inscrire().status_code, 200)
        utilisateur = database.charger_utilisateur(self.chemin, 1)
        self.assertNotEqual(utilisateur.mot_de_passe_hash, "une phrase de test 123")
        self.assertTrue(utilisateur.verifier_mot_de_passe("une phrase de test 123"))
        self.assertFalse(utilisateur.verifier_mot_de_passe("mauvais mot de passe"))
        with database.connexion(self.chemin) as base:
            self.assertEqual(base.execute("SELECT COUNT(*) FROM caves WHERE utilisateur_id = 1").fetchone()[0], 1)
        second_client = self.app.test_client()
        self.assertEqual(self.inscrire("William", second_client).status_code, 400)
        self.assertEqual(self.inscrire("  WILLIAM  ", second_client).status_code, 400)
        with database.connexion(self.chemin) as base:
            self.assertEqual(base.execute("SELECT COUNT(*) FROM utilisateurs").fetchone()[0], 1)
            self.assertEqual(base.execute("SELECT COUNT(*) FROM caves").fetchone()[0], 1)

    def test_inscriptions_invalides(self):
        correct = {"identifiant": "William", "email": "william@exemple.fr",
                   "mot_de_passe": "une phrase de test 123", "confirmation": "une phrase de test 123"}
        for modification in ({"email": "sans-arobase"}, {"identifiant": "ab"},
                             {"mot_de_passe": "court", "confirmation": "court"},
                             {"confirmation": "une autre phrase"}):
            self.assertEqual(self.envoyer("/inscription", {**correct, **modification}).status_code, 400)
        with database.connexion(self.chemin) as base:
            self.assertEqual(base.execute("SELECT COUNT(*) FROM utilisateurs").fetchone()[0], 0)

    def test_connexion_deconnexion_et_renouvellement_session(self):
        self.inscrire()
        with self.client.session_transaction() as session:
            ancien_jeton = session["csrf_token"]
        self.assertEqual(self.envoyer("/deconnexion", {}).status_code, 200)
        self.assertEqual(self.client.get("/").status_code, 302)
        self.client.get("/connexion")
        with self.client.session_transaction() as session:
            self.assertNotIn("utilisateur_id", session)
            jeton_connexion = session["csrf_token"]
        self.assertNotEqual(ancien_jeton, jeton_connexion)
        self.assertEqual(self.envoyer("/connexion", {
            "identifiant": "WILLIAM", "mot_de_passe": "une phrase de test 123",
        }).status_code, 200)
        with self.client.session_transaction() as session:
            self.assertEqual(session["utilisateur_id"], 1)
            self.assertNotEqual(jeton_connexion, session["csrf_token"])
        self.assertEqual(self.client.get("/deconnexion").status_code, 405)
        self.assertEqual(self.client.get("/").headers["Cache-Control"], "no-store")

    def test_injection_sql_et_erreur_generique(self):
        self.inscrire()
        self.envoyer("/deconnexion", {})
        for identifiant, mot_de_passe in [
            ("William", "mauvais mot de passe"), ("inconnu", "mauvais mot de passe"),
            ("' OR 1=1 --", "mauvais mot de passe"), ("William", "' OR 1=1 --"),
        ]:
            reponse = self.envoyer("/connexion", {"identifiant": identifiant, "mot_de_passe": mot_de_passe})
            self.assertEqual(reponse.status_code, 401)
            self.assertIn("Identifiant ou mot de passe incorrect.", reponse.get_data(as_text=True))
            with self.client.session_transaction() as session:
                self.assertNotIn("utilisateur_id", session)
        with database.connexion(self.chemin) as base:
            self.assertEqual(base.execute("SELECT COUNT(*) FROM utilisateurs").fetchone()[0], 1)

    def test_routes_privees_refusent_les_visiteurs(self):
        self.assertEqual(self.client.get("/").status_code, 302)
        for url in ("/etageres", "/vins", "/bouteilles", "/deconnexion"):
            reponse = self.client.post(url, data={})
            self.assertEqual(reponse.status_code, 302)
            self.assertTrue(reponse.location.endswith("/connexion"))

    def test_formulaires_auth_et_deconnexion_proteges_csrf(self):
        for url in ("/inscription", "/connexion"):
            for jeton in ("", "incorrect", "é"):
                reponse = self.client.post(url, data={"csrf_token": jeton})
                self.assertEqual(reponse.status_code, 400)
        self.inscrire()
        self.assertEqual(self.client.post("/deconnexion", data={}).status_code, 400)
        with self.client.session_transaction() as session:
            self.assertEqual(session["utilisateur_id"], 1)

    def test_deux_caves_privees_et_catalogue_commun(self):
        self.inscrire()
        self.envoyer("/etageres", {"nom": "Étagère secrète Alice", "capacite": "3"})
        self.envoyer("/vins", {"domaine": "Domaine partagé", "nom_cuvee": "Vin commun",
                              "type": "rouge", "millesime": "2022", "region": "Savoie"})
        self.envoyer("/bouteilles", {"etagere_id": "1", "vin_id": "1", "quantite": "2", "prix_achat": "987.65"})
        autre = self.app.test_client()
        self.inscrire("Bastien", autre)
        page = autre.get("/?utilisateur_id=1").get_data(as_text=True)
        self.assertNotIn("Étagère secrète Alice", page)
        self.assertNotIn("987,65", page)
        self.assertIn("Vin commun", page)
        # Forger l'identifiant d'une étagère dans le POST ne donne aucun droit.
        for etagere_id in ("1", "999"):
            refus = self.envoyer("/bouteilles", {"etagere_id": etagere_id, "vin_id": "1",
                                  "quantite": "1", "prix_achat": "1", "utilisateur_id": "1"}, autre)
            self.assertEqual(refus.status_code, 404)
        self.assertEqual(database.consulter_cave(self.chemin, 1)["stats"]["bouteilles"], 2)
        self.assertEqual(database.consulter_cave(self.chemin, 2)["stats"]["bouteilles"], 0)
        # Même nom possible dans deux caves ; le propriétaire vient de la session.
        reponse = self.envoyer("/etageres", {"nom": "Étagère secrète Alice", "capacite": "5", "utilisateur_id": "1"}, autre)
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(len(database.consulter_cave(self.chemin, 2)["etageres"]), 1)
        self.assertEqual(len(database.consulter_cave(self.chemin, 1)["etageres"]), 1)


class TestMigration(unittest.TestCase):
    def test_ancien_stock_conserve_et_attribue_une_seule_fois(self):
        with tempfile.TemporaryDirectory() as dossier:
            chemin = str(Path(dossier) / "ancienne.sqlite3")
            with database.connexion(chemin) as base:
                base.executescript("""
                    CREATE TABLE etageres (id INTEGER PRIMARY KEY, nom TEXT NOT NULL,
                        nom_cle TEXT NOT NULL UNIQUE, capacite_max INTEGER NOT NULL);
                    CREATE TABLE vins (id INTEGER PRIMARY KEY, domaine TEXT NOT NULL, nom_cuvee TEXT NOT NULL,
                        type TEXT NOT NULL, millesime INTEGER NOT NULL, region TEXT NOT NULL, identite TEXT NOT NULL UNIQUE);
                    CREATE TABLE bouteilles (id INTEGER PRIMARY KEY, vin_id INTEGER REFERENCES vins(id),
                        etagere_id INTEGER REFERENCES etageres(id), prix_centimes INTEGER, date_ajout TEXT);
                    INSERT INTO etageres VALUES (7, 'Mes rouges', 'mes rouges', 3);
                    INSERT INTO vins VALUES (11, 'Domaine', 'Cuvée', 'rouge', 2022, 'Savoie', 'ancienne-cle');
                    INSERT INTO bouteilles VALUES (42, 11, 7, 1250, '2026-10-21');
                """)
            database.initialiser(chemin)
            sauvegardes = list(Path(dossier).glob('cavissimo-avant-comptes-*.sqlite3'))
            self.assertEqual(len(sauvegardes), 1)
            with database.connexion(sauvegardes[0]) as base:
                self.assertEqual(base.execute("SELECT COUNT(*) FROM bouteilles").fetchone()[0], 1)
            premier = database.inscrire(chemin, "Premier", "premier@exemple.fr", "une phrase de test 123")
            second = database.inscrire(chemin, "Second", "second@exemple.fr", "une phrase de test 123")
            cave = database.consulter_cave(chemin, premier.id)
            self.assertEqual(cave["bouteilles"][0]["id"], 42)
            self.assertEqual(cave["etageres"][0]["id"], 7)
            self.assertEqual(cave["stats"]["valeur_centimes"], 1250)
            self.assertEqual(database.consulter_cave(chemin, second.id)["stats"]["bouteilles"], 0)
            database.creer_etagere(chemin, second.id, "Mes rouges", 5)
            database.initialiser(chemin)
            self.assertEqual(len(list(Path(dossier).glob('cavissimo-avant-comptes-*.sqlite3'))), 1)
            with database.connexion(chemin) as base:
                self.assertEqual(base.execute("PRAGMA foreign_key_check").fetchall(), [])


if __name__ == "__main__":
    unittest.main()
