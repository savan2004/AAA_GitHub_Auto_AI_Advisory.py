from flask import Flask, request, render_template_string, redirect, url_for
from user_tracker import UserTracker
from rag_system import RAGSystem
from config import Config

class AdminPanel:
    def __init__(self, user_tracker: UserTracker, rag_system: RAGSystem):
        self.app = Flask(__name__)
        self.user_tracker = user_tracker
        self.rag_system = rag_system
        self.setup_routes()
    
    def setup_routes(self):
        @self.app.route('/', methods=['GET', 'POST'])
        def login():
            if request.method == 'POST':
                if request.form.get('password') == Config.ADMIN_PASSWORD:
                    return redirect(url_for('dashboard'))
                return render_template_string('<h1>Invalid Password</h1><a href="/">Back</a>')
            return render_template_string('''
                <h1>Admin Panel Login</h1>
                <form method="post">
                    Password: <input type="password" name="password"><br>
                    <input type="submit" value="Login">
                </form>
            ''')
        
        @self.app.route('/dashboard')
        def dashboard():
            users = self.user_tracker.get_all_users()
            queries = self.user_tracker.get_all_queries()
            rag_data = self.rag_system.get_all_data()
            return render_template_string('''
                <h1>Admin Dashboard</h1>
                <h2>Users</h2>
                <table border="1">
                    <tr><th>User ID</th><th>Username</th><th>Premium</th><th>Queries Today</th><th>Total Queries</th><th>Last Date</th></tr>
                    {% for user in users %}
                    <tr><td>{{ user.user_id }}</td><td>{{ user.username }}</td><td>{{ user.is_premium }}</td><td>{{ user.queries_today }}</td><td>{{ user.total_queries }}</td><td>{{ user.last_query_date }}</td></tr>
                    {% endfor %}
                </table>
                <h2>Queries</h2>
                <table border="1">
                    <tr><th>ID</th><th>User ID</th><th>Query</th><th>Response</th><th>Timestamp</th></tr>
                    {% for query in queries %}
                    <tr><td>{{ query.id }}</td><td>{{ query.user_id }}</td><td>{{ query.query }}</td><td>{{ query.response }}</td><td>{{ query.timestamp }}</td></tr>
                    {% endfor %}
                </table>
                <h2>RAG Data</h2>
                <table border="1">
                    <tr><th>ID</th><th>Symbol</th><th>Date</th><th>LTP</th><th>RSI</th><th>Trend</th><th>News</th><th>Analysis</th></tr>
                    {% for data in rag_data %}
                    <tr><td>{{ data.id }}</td><td>{{ data.symbol }}</td><td>{{ data.date }}</td><td>{{ data.ltp }}</td><td>{{ data.rsi }}</td><td>{{ data.trend }}</td><td>{{ data.news }}</td><td>{{ data.analysis }}</td></tr>
                    {% endfor %}
                </table>
                <h2>Pricing Management</h2>
                <form method="post" action="/update_pricing">
                    Free Queries/Day: <input type="number" name="free_queries" value="{{ free_queries }}"><br>
                    Premium Price (INR): <input type="number" step="0.01" name="premium_price" value="{{ premium_price }}"><br>
                    <input type="submit" value="Update">
                </form>
                <a href="/">Logout</a>
            ''', users=users, queries=queries, rag_data=rag_data, free_queries=Config.FREE_QUERIES_PER_DAY, premium_price=Config.PREMIUM_PRICE)
        
        @self.app.route('/update_pricing', methods=['POST'])
        def update_pricing():
            Config.FREE_QUERIES_PER_DAY = int(request.form.get('free_queries'))
            Config.PREMIUM_PRICE = float(request.form.get('premium_price'))
            return redirect(url_for('dashboard'))
    
    def run(self, host='0.0.0.0', port=5000):
        self.app.run(host=host, port=port, debug=False)