import streamlit as st
from streamlit_option_menu import option_menu
import gspread
from google.oauth2.service_account import Credentials
import datetime
import pandas as pd

# --- Classes ---
class Account:
    # base class for a login-capable account (User and Admin inherit from this)
    def __init__(self, account_id, username, password):
        self.__accountId = account_id
        self.__username = username
        self.__password = password

    def getAccountId(self):
        return self.__accountId

    def getUsername(self):
        return self.__username

    def getPassword(self):
        return self.__password

    def login(self, entered_password):
        # true if the supplied password matches the stored one
        return self.__password == entered_password

    def logout(self):
        return True

class User(Account):
    # a regular user account that can rate movies,view recommendations and trending movies.
    def __init__(self, account_id, username, password, user_email="", watch_history=None):
        super().__init__(account_id, username, password)
        self.__userEmail = user_email
        self.__watchHistory = watch_history or []

    def getUserEmail(self):
        return self.__userEmail

    def rateMovie(self, movie_id, score):
        # record a rating locally (the ratings sheet is updated separately via Rating.save)
        rating = {"movieId": movie_id, "score": score}
        self.__watchHistory.append(rating)
        return rating

    def getUserHistory(self):
        return self.__watchHistory

    def viewRecommendations(self, recommendations):
        return recommendations

class Admin(Account):
    # admin have privileges for managing movies and viewing stats.
    def __init__(self, account_id, login_key, admin_email=""):
        super().__init__(account_id, username="admin", password="")
        self.__loginKey = login_key
        self.__adminEmail = admin_email

    def checkKey(self, entered_key):
        # admins authenticate with a shared key instead of username/password
        return self.__loginKey == entered_key

    def addMovie(self, movie, movies_worksheet):
        # append a new row to the Movies sheet
        movies_worksheet.append_row([movie.getMovieId(), movie.getTitle(), movie.getGenre(), movie.getAvgRating()])
        return movie

    def editMovie(self, movie, movies_worksheet):
        # find the movie's existing row by its ID and overwrite that row in place
        cell = movies_worksheet.find(movie.getMovieId())
        row_num = cell.row
        movies_worksheet.update(f"A{row_num}:D{row_num}", [[movie.getMovieId(), movie.getTitle(), movie.getGenre(), movie.getAvgRating()]])
        return movie

    def removeMovie(self, movie_id, movies_worksheet):
        # find the movie's row by its ID and delete it
        cell = movies_worksheet.find(movie_id)
        movies_worksheet.delete_rows(cell.row)
        return movie_id

    def viewEngagementStats(self, ratings_data, movie_list):
        # count how many times each movie has been rated
        counts = {}
        for r in ratings_data:
            counts[r["movieId"]] = counts.get(r["movieId"], 0) + 1

        # build a stats entry only for movies that have at least one rating
        stats = []
        for movie in movie_list:
            if movie.getMovieId() in counts:
                stats.append({"title": movie.getTitle(), "timesRated": counts[movie.getMovieId()]})

        # most-rated movies first
        stats.sort(key=lambda x: x["timesRated"], reverse=True)
        return stats

class Movie:
    # represents a single movie and its average rating.
    def __init__(self, movie_id, title, genre, avg_rating=0.0):
        self.__movieId = movie_id
        self.__title = title
        self.__genre = genre
        self.__avgRating = avg_rating

    def getMovieId(self):
        return self.__movieId

    def getTitle(self):
        return self.__title

    def getGenre(self):
        return self.__genre

    def getAvgRating(self):
        return self.__avgRating

    def getDetails(self):
        # plain dict form, used for display and for RecommendationEngine results
        return {
            "movieId": self.__movieId,
            "title": self.__title,
            "genre": self.__genre,
            "avgRating": self.__avgRating,
        }

    def updateAvgRating(self, all_scores):
        # recompute the average from a fresh list of scores (does nothing if empty)
        if all_scores:
            self.__avgRating = sum(all_scores) / len(all_scores)
        return self.__avgRating

class Rating:
    # a single user-to-movie rating, saved as a new row in the Ratings sheet.
    def __init__(self, rating_id, user_id, movie_id, score, timestamp=None):
        self.__ratingId = rating_id
        self.__userId = user_id
        self.__movieId = movie_id
        self.__score = score
        # default to the current time if no timestamp was supplied
        self.__timestamp = timestamp or datetime.datetime.now().isoformat()

    def save(self, ratings_worksheet):
        # append this rating as a new row in the Ratings sheet
        ratings_worksheet.append_row([
            self.__ratingId,
            self.__userId,
            self.__movieId,
            self.__score,
            self.__timestamp,
        ])

class RecommendationEngine:
    # generates simple genre-based recommendations and trending lists.
    def __init__(self, movie_list, rating_data):
        self.__movieList = movie_list
        self.__ratingData = rating_data

    # find the score of a candidate movie for a user
    def calculateScore(self, user_id, movie_id):
        # get this user's past ratings
        user_ratings = [r for r in self.__ratingData if r["userId"] == user_id]
        if not user_ratings:
            return 0.0

        # find the candidate movie's genre
        candidate = next((m for m in self.__movieList if m.getMovieId() == movie_id), None)
        if not candidate:
            return 0.0

        # average the user's scores for movies in the same genre
        same_genre_scores = []
        # loop through past ratings and find those that match the candidate's genre
        for r in user_ratings:
            rated_movie = next((m for m in self.__movieList if m.getMovieId() == r["movieId"]), None)
            if rated_movie and rated_movie.getGenre() == candidate.getGenre():
                same_genre_scores.append(r["score"])

        if not same_genre_scores:
            return 0.0
        return sum(same_genre_scores) / len(same_genre_scores)

    def generateRecommendations(self, user_id, top_n=5):
        # find rated movies by this user
        rated_movie_ids = {r["movieId"] for r in self.__ratingData if r["userId"] == user_id}
        # loop through all movies and find those not rated by this user
        unrated_movies = [m for m in self.__movieList if m.getMovieId() not in rated_movie_ids]

        # call calculateScore for each unrated movie
        scored = [(m, self.calculateScore(user_id, m.getMovieId())) for m in unrated_movies]
        # sort by score descending
        scored.sort(key=lambda x: x[1], reverse=True)

        # return just the top N as plain dicts
        return [m.getDetails() for m, score in scored[:top_n]]

    def getTrending(self, top_n=5):
        counts = {}
        # loop through every single rating in the system
        for r in self.__ratingData:
            counts[r["movieId"]] = counts.get(r["movieId"], 0) + 1

        # sort the movie IDs by count descending and take the top 5
        sorted_ids = sorted(counts, key=counts.get, reverse=True)[:top_n]
        # find the movies and return the details
        trending_movies = [m for m in self.__movieList if m.getMovieId() in sorted_ids]
        return [m.getDetails() for m in trending_movies]

# --- Database connection ---
@st.cache_resource
def get_sheet():
    # authenticate with Google using the service account creds stored in secrets.toml
    # and open the shared spreadsheet
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scopes)
    client = gspread.authorize(creds)
    return client.open("MRS_Database")

sheet = get_sheet()
# one worksheet ("tab") per data type, acting as our tables
users_ws = sheet.worksheet("Users")
ratings_ws = sheet.worksheet("Ratings")
movies_ws = sheet.worksheet("Movies")

# look up a user by username, returns None if not found
def get_user(username):
    records = users_ws.get_all_records()
    for row in records:
        if row["username"] == username:
            return User(row.get("accountId", ""), str(row["username"]), str(row["password"]), row.get("email", ""))
    return None

# sign up a new user and add to the database
def add_user(username, password, email):
    account_id = f"u{len(users_ws.get_all_records()) + 1}"
    new_user = User(account_id, username, password, email)
    users_ws.append_row([new_user.getAccountId(), new_user.getUsername(), new_user.getPassword(), new_user.getUserEmail()])
    return new_user

# load all movies from the Movies sheet into Movie objects
def get_all_movies():
    records = movies_ws.get_all_records()
    movies = []
    for row in records:
        movie = Movie(row["movieId"], row["title"], row["genre"], float(row["avgRating"]))
        movies.append(movie)
    return movies

# load all ratings from the Ratings sheet as a list of dicts
def get_all_ratings():
    records = ratings_ws.get_all_records()
    ratings = []
    for row in records:
        ratings.append({
            "ratingId": row["ratingId"],
            "userId": row["userId"],
            "movieId": row["movieId"],
            "score": float(row["score"]),
            "timestamp": row["timestamp"],
        })
    return ratings

# --- Page config ---
st.set_page_config(page_title="Movie Recommendation System", layout="centered")

# --- Config: admin key (password in secrets.toml) ---
ADMIN_KEY = st.secrets["admin"]["admin_key"]

# --- Session state defaults ---
# these persist across reruns for the duration of the browser session
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "role" not in st.session_state:
    st.session_state.role = None  # "user" or "admin"
if "username" not in st.session_state:
    st.session_state.username = None


def show_login_page():
    st.title("Movie Recommendation System")

    tab_user, tab_admin = st.tabs(["User login", "Admin login"])

    # ---------------- USER LOGIN ----------------
    with tab_user:
        login_tab, signup_tab = st.tabs(["Log in", "Sign up"])

        with login_tab:
            username = st.text_input("Username", key="user_username")
            password = st.text_input("Password", type="password", key="user_password")

            if st.button("Log in", key="user_login_btn", use_container_width=True):
                user = get_user(username)
                if user and user.login(password):
                    st.session_state.logged_in = True
                    st.session_state.role = "user"
                    st.session_state.username = username
                    st.rerun()
                else:
                    st.error("Incorrect username or password.")

        with signup_tab:
            new_username = st.text_input("Choose a username", key="signup_username")
            new_password = st.text_input("Choose a password", type="password", key="signup_password")
            new_email = st.text_input("Email", key="signup_email")

            if st.button("Sign up", key="signup_btn", use_container_width=True):
                if not new_username or not new_password:
                    st.error("Username and password are required.")
                elif get_user(new_username):
                    st.error("That username is already taken.")
                else:
                    add_user(new_username, new_password, new_email)
                    st.success("Account created! Please log in.")

    # ---------------- ADMIN LOGIN ----------------
    with tab_admin:
        st.markdown("#### Admin access")
        st.write("Admin login key")
        admin_key_input = st.text_input(
            "Admin login key",
            placeholder="Enter unique admin key",
            type="password",
            key="admin_key_input",
            label_visibility="collapsed",
        )

        if st.button("Unlock admin console", key="admin_login_btn", use_container_width=True):
            admin = Admin("admin1", ADMIN_KEY)
            if admin.checkKey(admin_key_input):
                st.session_state.logged_in = True
                st.session_state.role = "admin"
                st.session_state.username = "admin"
                st.rerun()
            else:
                st.error("Incorrect admin key. Please try again.")

        st.caption("No account needed — just the key set in config.")

def show_sidebar():
    # renders the sidebar nav (page differs by role) and the log out button.
    with st.sidebar:
        st.title("MRS")
        st.divider()
 
        if st.session_state.role == "admin":
            pages = ["Manage movies", "Engagement analytics"]
        else:
            pages = ["Search & rate", "Recommended for you", "Trending", "Watch history", "Insights"]
 
        selected_page = option_menu(
            menu_title=None,
            options=pages,
            icons=["search", "star", "fire", "clock-history", "bar-chart"] if st.session_state.role != "admin" else ["film", "graph-up"],
            default_index=0,
            styles={
                "nav-link-selected": {"background-color": "#1DB954", "font-weight": "normal"},
            },
        )
 
        st.divider()
        # logout button clears session state and reruns the app to show the login page
        if st.button("Log out", use_container_width=True):
            st.session_state.logged_in = False
            st.session_state.role = None
            st.session_state.username = None
            st.rerun()
 
    return selected_page

# --- User dashboard ---
def show_search_and_rate():
    # search movies by title/genre and submit a star rating for unrated ones.
    st.subheader("Search & rate movies")

    # show success message if one was saved from before the last rerun
    if st.session_state.get("rating_success"):
        st.success(st.session_state.rating_success)
        st.session_state.rating_success = None

    search_query = st.text_input("Search by title or genre", placeholder="e.g. Sci-Fi, Dune...")

    all_movies = get_all_movies()
    user = get_user(st.session_state.username)
    all_ratings = get_all_ratings()

    # build a lookup of {movieId: score} for movies this user already rated
    user_rated = {
        r["movieId"]: r["score"]
        for r in all_ratings
        if r["userId"] == user.getAccountId()
    }

    if search_query:
        filtered_movies = [
            m for m in all_movies
            if search_query.lower() in m.getTitle().lower() or search_query.lower() in m.getGenre().lower()
        ]
    else:
        filtered_movies = all_movies

    st.caption(f"{len(filtered_movies)} movie(s) found")

    grid_cols = st.columns(3)

    for index, movie in enumerate(filtered_movies):
        with grid_cols[index % 3]:
            with st.container(border=True):
                st.markdown(f"**{movie.getTitle()}**")
                st.caption(f"{movie.getGenre()} · Avg rating {movie.getAvgRating():.1f}")

                if movie.getMovieId() in user_rated:
                    st.markdown(
                        f"<span style='color: #1DB954;'>You rated this {int(user_rated[movie.getMovieId()])} stars</span>",
                        unsafe_allow_html=True,
                    )
                else:
                    score = st.feedback("stars", key=f"rate_{movie.getMovieId()}")

                    if score is not None:
                        if st.button("Submit rating", key=f"submit_{movie.getMovieId()}", use_container_width=True):
                            rating_id = f"r{len(ratings_ws.get_all_records()) + 1}"
                            new_rating = Rating(rating_id, user.getAccountId(), movie.getMovieId(), score + 1)
                            new_rating.save(ratings_ws)

                            # recalculate this movie's real average rating from all scores
                            updated_ratings = get_all_ratings()
                            movie_scores = [r["score"] for r in updated_ratings if r["movieId"] == movie.getMovieId()]
                            movie.updateAvgRating(movie_scores)
                            admin_for_update = Admin("admin1", ADMIN_KEY)
                            admin_for_update.editMovie(movie, movies_ws)

                            st.session_state.rating_success = f"Rated {movie.getTitle()} {score + 1} stars!"
                            st.rerun()

def show_recommended():
    # personalised recommendations based on the user's genre preferences.
    st.subheader("Recommended for you")

    user = get_user(st.session_state.username)
    all_movies = get_all_movies()
    all_ratings = get_all_ratings()

    engine = RecommendationEngine(all_movies, all_ratings)
    recommendations = engine.generateRecommendations(user.getAccountId(), top_n=5)

    if not recommendations:
        st.info("Rate a few movies first so we can learn your taste!")
        return

    grid_cols = st.columns(3)

    for index, movie in enumerate(recommendations):
        with grid_cols[index % 3]:
            with st.container(border=True):
                st.markdown(f"**{movie['title']}**")
                st.caption(f"{movie['genre']} · Avg rating {movie['avgRating']:.1f}")

def show_trending():
    # movies ranked by how many times they've been rated, platform-wide.
    st.subheader("Trending movies")

    all_movies = get_all_movies()
    all_ratings = get_all_ratings()

    engine = RecommendationEngine(all_movies, all_ratings)
    trending_movies = engine.getTrending(top_n=6)

    if not trending_movies:
        st.info("No ratings yet — trending movies will appear once users start rating.")
        return

    grid_cols = st.columns(3)

    for index, movie in enumerate(trending_movies):
        with grid_cols[index % 3]:
            with st.container(border=True):
                st.markdown(f"**{movie['title']}**")
                st.caption(f"{movie['genre']} · Avg rating {movie['avgRating']:.1f}")

def show_watch_history():
    st.subheader("Your watch history")

    user = get_user(st.session_state.username)
    all_movies = get_all_movies()
    all_ratings = get_all_ratings()

    # filter to just this user's ratings, newest first
    my_ratings = [r for r in all_ratings if r["userId"] == user.getAccountId()]
    my_ratings.sort(key=lambda r: r["timestamp"], reverse=True)

    if not my_ratings:
        st.info("You haven't rated any movies yet. Head to Search & rate to get started!")
        return

    st.caption(f"{len(my_ratings)} movie(s) rated")

    # display each rated movie with its title, genre, rating, and timestamp
    for r in my_ratings:
        movie = next((m for m in all_movies if m.getMovieId() == r["movieId"]), None)
        if not movie:
            continue

        with st.container(border=True):
            col1, col2 = st.columns([3, 1])
            with col1:
                st.markdown(f"**{movie.getTitle()}**")
                st.caption(f"{movie.getGenre()} · Rated on {r['timestamp'][:10]}")
            with col2:
                st.markdown(f"{int(r['score'])} / 5")

def show_insights():
    # 2 charts showing top rated movies overalln and the user's ratings by genre.
    st.subheader("Insights")

    all_movies = get_all_movies()
    all_ratings = get_all_ratings()
    user = get_user(st.session_state.username)

    # --- Chart 1: top rated movies platform-wide ---
    st.markdown("**Top rated movies**")
    top_movies = sorted(all_movies, key=lambda m: m.getAvgRating(), reverse=True)[:8]

    if top_movies:
        chart_data = pd.DataFrame({
            "Movie": [m.getTitle() for m in top_movies],
            "Avg rating": [m.getAvgRating() for m in top_movies],
        }).set_index("Movie")
        st.bar_chart(chart_data)
    else:
        st.info("No movies available yet.")

    st.divider()

    # --- Chart 2: User's ratings by genre ---
    st.markdown("**Your ratings by genre**")
    my_ratings = [r for r in all_ratings if r["userId"] == user.getAccountId()]

    if my_ratings:
        genre_scores = {}
        for r in my_ratings:
            movie = next((m for m in all_movies if m.getMovieId() == r["movieId"]), None)
            if movie:
                genre_scores.setdefault(movie.getGenre(), []).append(r["score"])

        genre_avg = {genre: sum(scores) / len(scores) for genre, scores in genre_scores.items()}

        genre_chart_data = pd.DataFrame({
            "Genre": list(genre_avg.keys()),
            "Your avg score": list(genre_avg.values()),
        }).set_index("Genre")
        st.bar_chart(genre_chart_data)
    else:
        st.info("Rate a few movies to see your genre breakdown!")

# --- Admin dashboard ---
def show_manage_movies():
    # admin CRUD screen for movies: add, edit, or remove entries.
    st.subheader("Manage movies")

    admin = Admin("admin1", ADMIN_KEY)

    if st.session_state.get("movie_success"):
        st.success(st.session_state.movie_success)
        st.session_state.movie_success = None

    tab_add, tab_edit, tab_remove = st.tabs(["Add movie", "Edit movie", "Remove movie"])

    all_movies = get_all_movies()

    # generate the next movieId automatically, e.g. mv1, mv2, mv3...
    def get_next_movie_id():
        existing_numbers = []
        for m in all_movies:
            digits = "".join(ch for ch in m.getMovieId() if ch.isdigit())
            if digits:
                existing_numbers.append(int(digits))
        next_number = max(existing_numbers, default=0) + 1
        return f"mv{next_number}"

    # ---------------- Add movies ----------------
    with tab_add:
        new_title = st.text_input("Title", key="add_movie_title")
        new_genre = st.text_input("Genre", key="add_movie_genre")
        new_rating = st.number_input("Initial avg rating", min_value=0.0, max_value=5.0, value=0.0, step=0.1, key="add_movie_rating")

        if st.button("Add movie", key="add_movie_btn", use_container_width=True):
            if not new_title or not new_genre:
                st.error("Title and genre are required.")
            else:
                new_id = get_next_movie_id()
                new_movie = Movie(new_id, new_title, new_genre, new_rating)
                admin.addMovie(new_movie, movies_ws)
                st.session_state.movie_success = f"Added '{new_title}'."
                st.rerun()

    # ---------------- Edit movies ----------------
    with tab_edit:
        if not all_movies:
            st.info("No movies to edit yet.")
        else:
            movie_options = {m.getTitle(): m for m in all_movies}
            selected_title = st.selectbox("Select a movie to edit", list(movie_options.keys()), key="edit_movie_select")
            selected_movie = movie_options[selected_title]
            movie_id = selected_movie.getMovieId()

            edit_title = st.text_input("Title", value=selected_movie.getTitle(), key=f"edit_movie_title_{movie_id}")
            edit_genre = st.text_input("Genre", value=selected_movie.getGenre(), key=f"edit_movie_genre_{movie_id}")
            edit_rating = st.number_input("Avg rating", min_value=0.0, max_value=5.0, value=selected_movie.getAvgRating(), step=0.1, key=f"edit_movie_rating_{movie_id}")

            if st.button("Save changes", key=f"edit_movie_btn_{movie_id}", use_container_width=True):
                updated_movie = Movie(movie_id, edit_title, edit_genre, edit_rating)
                admin.editMovie(updated_movie, movies_ws)
                st.session_state.movie_success = f"Updated '{edit_title}'."
                st.rerun()

    # ---------------- Remove movies ----------------
    with tab_remove:
        if not all_movies:
            st.info("No movies to remove yet.")
        else:
            movie_options = {m.getTitle(): m for m in all_movies}
            selected_title = st.selectbox("Select a movie to remove", list(movie_options.keys()), key="remove_movie_select")
            selected_movie = movie_options[selected_title]

            st.warning(f"This will permanently remove '{selected_movie.getTitle()}'.")
            if st.button("Remove movie", key="remove_movie_btn", use_container_width=True):
                admin.removeMovie(selected_movie.getMovieId(), movies_ws)
                st.session_state.movie_success = f"Removed '{selected_movie.getTitle()}'."
                st.rerun()

def show_engagement_analytics():
    # admin stats: total ratings, most-watched movies, and popular genres.
    st.subheader("Engagement analytics")

    admin = Admin("admin1", ADMIN_KEY)
    all_movies = get_all_movies()
    all_ratings = get_all_ratings()

    stats = admin.viewEngagementStats(all_ratings, all_movies)

    if not stats:
        st.info("No ratings yet — engagement stats will appear once users start rating movies.")
        return

    total_ratings = len(all_ratings)
    total_movies_rated = len(stats)

    # --- Metrics ---
    col1, col2 = st.columns(2)
    with col1:
        st.metric("Total ratings submitted", total_ratings)
    with col2:
        st.metric("Movies with at least 1 rating", total_movies_rated)

    st.divider()
    st.markdown("**Most-watched movies**")

    # get the top 10 most-watched movies
    top_stats = stats[:10]
    chart_data = pd.DataFrame({
        "Movie": [s["title"] for s in top_stats],
        "Times rated": [s["timesRated"] for s in top_stats],
    }).set_index("Movie")
    st.bar_chart(chart_data)

    st.divider()
    st.markdown("**Most popular genres**")

    genre_counts = {}
    for r in all_ratings:
        movie = next((m for m in all_movies if m.getMovieId() == r["movieId"]), None)
        if movie:
            genre_counts[movie.getGenre()] = genre_counts.get(movie.getGenre(), 0) + 1

    if genre_counts:
        genre_chart_data = pd.DataFrame({
            "Genre": list(genre_counts.keys()),
            "Ratings": list(genre_counts.values()),
        }).set_index("Genre")
        st.bar_chart(genre_chart_data)

    st.divider()
    st.markdown("**Full breakdown**")

    # display the full breakdown in a table
    st.dataframe(
        pd.DataFrame(stats).rename(columns={"title": "Movie", "timesRated": "Times rated"}),
        use_container_width=True,
        hide_index=True,
    )

# --- Main app router ---
def show_main_app():
    # Shows the sidebar nav and routes to the selected page based on role.
    page = show_sidebar()
    st.title("Movie Recommendation System")

    if st.session_state.role == "admin":
        if page == "Manage movies":
            show_manage_movies()
        elif page == "Engagement analytics":
            show_engagement_analytics()
    else:
        if page == "Search & rate":
            show_search_and_rate()
        elif page == "Recommended for you":
            show_recommended()
        elif page == "Trending":
            show_trending()
        elif page == "Watch history":
            show_watch_history()
        elif page == "Insights":
            show_insights()

# --- Router ---
# entry point: show the app if logged in, otherwise show the login page
if st.session_state.logged_in:
    show_main_app()
else:
    show_login_page()