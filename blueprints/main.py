from flask import Blueprint, render_template, g, redirect, url_for

from authz import login_required

main_bp = Blueprint('main', __name__)


@main_bp.route('/')
def index():
    if g.get('superadmin'):
        return redirect(url_for('super.tenants'))
    if not g.get('user'):
        return redirect(url_for('auth.login'))
    return redirect(url_for('main.home'))


@main_bp.route('/home')
@login_required
def home():
    return render_template('home.html')
