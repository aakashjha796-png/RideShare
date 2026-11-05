
from flask import Flask, render_template, request, redirect, url_for, session, flash
from database import init_db, get_connection
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from datetime import date

app = Flask(__name__)
app.secret_key = "rideshare-demo-secret-key"

init_db()


# Login required decorator
def login_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            flash("Please login first.", "error")
            return redirect(url_for("login"))
        return func(*args, **kwargs)
    return wrapper


# Home page
@app.route("/")
def index():
    return render_template("index.html")


# Registration
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not name or not email or len(password) < 6:
            flash("Enter valid details. Password must be at least 6 characters.", "error")
            return redirect(url_for("register"))

        conn = get_connection()

        try:
            conn.execute(
                "INSERT INTO users (name, email, password) VALUES (?, ?, ?)",
                (name, email, generate_password_hash(password))
            )
            conn.commit()
            flash("Registration successful! Please login.", "success")
            return redirect(url_for("login"))

        except Exception:
            flash("This email may already be registered.", "error")
            return redirect(url_for("register"))

        finally:
            conn.close()

    return render_template("register.html")


# Login
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        conn = get_connection()
        user = conn.execute(
            "SELECT * FROM users WHERE email = ?", (email,)
        ).fetchone()
        conn.close()

        if user and check_password_hash(user["password"], password):
            session["user_id"] = user["id"]
            session["user_name"] = user["name"]
            flash("Welcome back!", "success")
            return redirect(url_for("dashboard"))

        flash("Invalid email or password.", "error")

    return render_template("login.html")


# Logout
@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("index"))


# Dashboard
@app.route("/dashboard")
@login_required
def dashboard():
    conn = get_connection()

    my_rides = conn.execute(
        "SELECT * FROM rides WHERE driver_id = ? ORDER BY ride_date DESC",
        (session["user_id"],)
    ).fetchall()

    my_bookings = conn.execute("""
        SELECT bookings.*, rides.source, rides.destination,
               rides.ride_date, rides.ride_time, rides.price
        FROM bookings
        JOIN rides ON bookings.ride_id = rides.id
        WHERE bookings.passenger_id = ?
        ORDER BY rides.ride_date DESC
    """, (session["user_id"],)).fetchall()

    conn.close()

    return render_template(
        "dashboard.html",
        my_rides=my_rides,
        my_bookings=my_bookings
    )


# Find rides
@app.route("/find-ride")
@login_required
def find_ride():
    source = request.args.get("source", "").strip()
    destination = request.args.get("destination", "").strip()
    ride_date = request.args.get("ride_date", "").strip()

    conn = get_connection()

    rides = conn.execute("""
        SELECT rides.*, users.name AS driver_name,
        (rides.seats - COALESCE(
            (SELECT SUM(seats_booked)
             FROM bookings
             WHERE bookings.ride_id = rides.id
             AND bookings.status = 'Confirmed'), 0
        )) AS available_seats
        FROM rides
        JOIN users ON rides.driver_id = users.id
        WHERE rides.driver_id != ?
        AND rides.ride_date >= ?
        AND LOWER(rides.source) LIKE LOWER(?)
        AND LOWER(rides.destination) LIKE LOWER(?)
        ORDER BY rides.ride_date, rides.ride_time
    """, (
        session["user_id"],
        date.today().isoformat(),
        f"%{source}%",
        f"%{destination}%"
    )).fetchall()

    conn.close()

    rides = [
        ride for ride in rides
        if ride["available_seats"] > 0
    ]

    return render_template(
        "find_ride.html",
        rides=rides,
        source=source,
        destination=destination,
        ride_date=ride_date,
           today=date.today().isoformat()
    )


# Offer a ride
@app.route("/offer-ride", methods=["GET", "POST"])
@login_required
def offer_ride():
    if request.method == "POST":
        source = request.form.get("source", "").strip()
        destination = request.form.get("destination", "").strip()
        ride_date = request.form.get("ride_date", "")
        ride_time = request.form.get("ride_time", "")
        vehicle = request.form.get("vehicle", "").strip()

        try:
            seats = int(request.form.get("seats", "0"))
            price = float(request.form.get("price", "0"))
        except ValueError:
            flash("Enter valid seat and price values.", "error")
            return redirect(url_for("offer_ride"))

        if (not source or not destination or not ride_date
                or not ride_time or not vehicle
                or source.lower() == destination.lower()
                or ride_date < date.today().isoformat()
                or seats < 1 or seats > 8 or price < 0):
            flash("Please enter valid ride details.", "error")
            return redirect(url_for("offer_ride"))

        conn = get_connection()
        conn.execute("""
            INSERT INTO rides
            (driver_id, source, destination, ride_date,
             ride_time, seats, price, vehicle)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            session["user_id"], source, destination,
            ride_date, ride_time, seats, price, vehicle
        ))
        conn.commit()
        conn.close()

        flash("Your ride has been published!", "success")
        return redirect(url_for("dashboard"))

    return render_template(
    "offer_ride.html",
    today=date.today().isoformat()
)



# Book a ride
@app.route("/book/<int:ride_id>", methods=["POST"])
@login_required
def book_ride(ride_id):
    try:
        seats_requested = int(request.form.get("seats", "1"))
    except ValueError:
        flash("Invalid seat selection.", "error")
        return redirect(url_for("find_ride"))

    if seats_requested < 1:
        flash("Please select at least one seat.", "error")
        return redirect(url_for("find_ride"))

    conn = get_connection()

    try:
        conn.execute("BEGIN IMMEDIATE")

        ride = conn.execute(
            "SELECT * FROM rides WHERE id = ?", (ride_id,)
        ).fetchone()

        if not ride:
            flash("Ride not found.", "error")
        elif ride["driver_id"] == session["user_id"]:
            flash("You cannot book your own ride.", "error")
        elif ride["ride_date"] < date.today().isoformat():
            flash("This ride has already expired.", "error")
        else:
            existing = conn.execute("""
                SELECT id FROM bookings
                WHERE ride_id = ? AND passenger_id = ?
            """, (ride_id, session["user_id"])).fetchone()

            booked = conn.execute("""
                SELECT COALESCE(SUM(seats_booked), 0) AS total
                FROM bookings
                WHERE ride_id = ? AND status = 'Confirmed'
            """, (ride_id,)).fetchone()["total"]

            available = ride["seats"] - booked

            if existing:
                flash("You have already booked this ride.", "error")
            elif seats_requested > available:
                flash(f"Only {available} seats are available.", "error")
            else:
                conn.execute("""
                    INSERT INTO bookings
                    (ride_id, passenger_id, seats_booked)
                    VALUES (?, ?, ?)
                """, (ride_id, session["user_id"], seats_requested))

                flash("Ride booked successfully!", "success")

        conn.commit()

    except Exception:
        conn.rollback()
        flash("Unable to complete booking. Please try again.", "error")

    finally:
        conn.close()

    return redirect(url_for("find_ride"))


# My bookings
@app.route("/my-bookings")
@login_required
def my_bookings():
    conn = get_connection()

    bookings = conn.execute("""
        SELECT bookings.*, rides.source, rides.destination,
               rides.ride_date, rides.ride_time,
               rides.price, rides.vehicle,
               users.name AS driver_name
        FROM bookings
        JOIN rides ON bookings.ride_id = rides.id
        JOIN users ON rides.driver_id = users.id
        WHERE bookings.passenger_id = ?
        ORDER BY rides.ride_date DESC
    """, (session["user_id"],)).fetchall()

    conn.close()

    return render_template("my_bookings.html", bookings=bookings)


if __name__ == "__main__":
    app.run(debug=True)
