"""Flask reçoit les formulaires ; database.py enregistre les objets métier."""

import os
import secrets
from decimal import Decimal, InvalidOperation
from datetime import timedelta
from pathlib import Path

from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for

import database


def entier_positif(valeur):
    try:
        nombre = int(valeur)
    except (ValueError, TypeError):
        raise ValueError("La capacité, les identifiants, le millésime et la quantité doivent être des entiers.") from None
    if not 1 <= nombre <= 1_000_000:
        raise ValueError("Saisis un entier compris entre 1 et 1 000 000.")
    return nombre


def convertir_prix(valeur):
    try:
        prix = Decimal(valeur.strip().replace(",", "."))
    except (InvalidOperation, AttributeError):
        raise ValueError("Saisis un prix valide, par exemple 12,50.") from None
    if not prix.is_finite() or not 0 <= prix <= 1_000_000:
        raise ValueError("Le prix doit être compris entre 0 et 1 000 000 euros.")
    try:
        arrondi = prix.quantize(Decimal("0.01"))
    except InvalidOperation:
        raise ValueError("Saisis un prix comportant au maximum deux décimales.") from None
    if prix != arrondi:
        raise ValueError("Le prix doit comporter au maximum deux décimales.")
    return int(prix * 100)


def creer_app(config=None):
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.environ.get("CAVISSIMO_SECRET_KEY") or secrets.token_hex(32),
        DATABASE=str(Path(app.instance_path) / "cavissimo.sqlite3"),
        MAX_CONTENT_LENGTH=32_768,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    )
    if config:
        app.config.update(config)
    Path(app.config["DATABASE"]).parent.mkdir(parents=True, exist_ok=True)
    database.initialiser(app.config["DATABASE"])

    @app.before_request
    def verifier_formulaire():
        g.utilisateur = database.charger_utilisateur(app.config["DATABASE"], session.get("utilisateur_id"))
        publics = ("connexion", "inscription", "static", None)
        if request.endpoint not in publics and g.utilisateur is None:
            session.clear()
            return redirect(url_for("connexion"))
        if "csrf_token" not in session:
            session["csrf_token"] = secrets.token_hex(32)
        if request.method == "POST":
            jeton = request.form.get("csrf_token", "")
            if not jeton.isascii() or not secrets.compare_digest(jeton, session["csrf_token"]):
                abort(400, description="Formulaire expiré : recharge la page avant de réessayer.")

    @app.after_request
    def proteger_cache(reponse):
        if request.endpoint != "static":
            reponse.headers["Cache-Control"] = "no-store"
        return reponse

    def ouvrir_session(utilisateur):
        # Élimine l'ancienne session et renouvelle aussi le jeton du formulaire.
        session.clear()
        session["utilisateur_id"] = utilisateur.id
        session["csrf_token"] = secrets.token_hex(32)
        session.permanent = True

    @app.route("/inscription", methods=["GET", "POST"])
    def inscription():
        if g.utilisateur is not None:
            return redirect(url_for("accueil"))
        if request.method == "POST":
            try:
                mot_de_passe = request.form.get("mot_de_passe", "")
                if mot_de_passe != request.form.get("confirmation", ""):
                    raise ValueError("Les deux mots de passe ne correspondent pas.")
                utilisateur = database.inscrire(
                    app.config["DATABASE"], request.form.get("identifiant", ""),
                    request.form.get("email", ""), mot_de_passe,
                )
            except ValueError as erreur:
                flash(str(erreur), "erreur")
                return render_template("inscription.html"), 400
            ouvrir_session(utilisateur)
            flash("Compte créé ! Bienvenue dans votre cave.", "succes")
            return redirect(url_for("accueil"))
        return render_template("inscription.html")

    @app.route("/connexion", methods=["GET", "POST"])
    def connexion():
        if g.utilisateur is not None:
            return redirect(url_for("accueil"))
        if request.method == "POST":
            utilisateur = database.authentifier(
                app.config["DATABASE"], request.form.get("identifiant", ""),
                request.form.get("mot_de_passe", ""),
            )
            if utilisateur is None:
                flash("Identifiant ou mot de passe incorrect.", "erreur")
                return render_template("connexion.html"), 401
            ouvrir_session(utilisateur)
            return redirect(url_for("accueil"))
        return render_template("connexion.html")

    @app.post("/deconnexion")
    def deconnexion():
        session.clear()
        return redirect(url_for("connexion"))

    @app.template_filter("euros")
    def euros(centimes):
        return f"{centimes / 100:,.2f}".replace(",", " ").replace(".", ",") + " €"

    def afficher_cave(code=200):
        return render_template("index.html", **database.consulter_cave(app.config["DATABASE"], g.utilisateur.id)), code

    @app.get("/")
    def accueil():
        return afficher_cave()

    @app.post("/etageres")
    def creer_etagere():
        try:
            database.creer_etagere(app.config["DATABASE"], g.utilisateur.id, request.form.get("nom", ""),
                                   entier_positif(request.form.get("capacite")))
        except ValueError as erreur:
            flash(str(erreur), "erreur")
            return afficher_cave(400)
        flash("Étagère créée !", "succes")
        return redirect(url_for("accueil", _anchor="etageres"))

    @app.post("/vins")
    def creer_vin():
        try:
            nouveau = database.creer_vin(
                app.config["DATABASE"], request.form.get("domaine", ""),
                request.form.get("nom_cuvee", ""), request.form.get("type", ""),
                entier_positif(request.form.get("millesime")), request.form.get("region", ""),
            )
        except ValueError as erreur:
            flash(str(erreur), "erreur")
            return afficher_cave(400)
        flash("Vin ajouté au catalogue !" if nouveau else "Ce vin existe déjà : sélectionne-le pour ajouter tes bouteilles.", "succes")
        return redirect(url_for("accueil", _anchor="catalogue"))

    @app.post("/bouteilles")
    def ajouter_bouteilles():
        try:
            database.ajouter_bouteilles(
                app.config["DATABASE"], g.utilisateur.id, entier_positif(request.form.get("etagere_id")),
                entier_positif(request.form.get("vin_id")), entier_positif(request.form.get("quantite")),
                convertir_prix(request.form.get("prix_achat", "")),
            )
        except PermissionError:
            abort(404)
        except ValueError as erreur:
            flash(str(erreur), "erreur")
            return afficher_cave(400)
        flash("Bouteilles rangées dans la cave !", "succes")
        return redirect(url_for("accueil", _anchor="stock"))

    return app


if __name__ == "__main__":
    creer_app().run(host="127.0.0.1", port=5000)
