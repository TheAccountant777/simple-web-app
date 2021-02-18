# DataBase models for users and notes
from . import db
from flask_login import UserMixin
from sqlalchemy.sql import func

# This the User Table
class User(db.Model, UserMixin):
    # Lets define the columns in the User Table
    id = db.Column(db.Integer, primary_key = True)
    email = db.Column(db.String(150), unique = True)
    password = db.Column(db.String(150))
    first_name = db.Column(db.String(150))
    notes = db.relationship("Note") # This will store a list with all the notes of the user

# This the Notes Table
class Note(db.Model):
    id = db.Column(db.Integer, primary_key = True)
    data = db.Column(db.String(10000))
    date = db.Column(db.DateTime(timezone = True), default = func.now())
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))