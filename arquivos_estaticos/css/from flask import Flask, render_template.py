from flask import Flask, render_template

# ... suas configurações existentes ...

@app.route("/")
def home():
    # Renderiza a nova página de apresentação
    return render_template("index.html")

@app.route("/login")
def login():
    return render_template("01_login.html")