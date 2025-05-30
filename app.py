from flask import Flask, request, render_template
import sqlite3
import pandas as pd
import google.generativeai as genai
import os
import json
import re
from dotenv import load_dotenv


app = Flask(__name__)

load_dotenv()  # Loads environment variables from .env file

api_key = os.getenv("GEMINI_API_KEY")
genai.configure(api_key=api_key)
model = genai.GenerativeModel("gemini-2.0-flash")

DB_PATH = 'database/database.db'
SCHEMA_CACHE = 'database/db_schema.json'

def extract_sql(text):
    sql_match = re.search(r"```sql(.*?)```", text, re.DOTALL | re.IGNORECASE)
    sql = sql_match.group(1).strip() if sql_match else None

    explanation = text.replace(sql_match.group(0), '').strip() if sql_match else text.strip()
    return sql, explanation

# Utility: Load DB schema (from cache or generate it)
def load_db_schema():
    if os.path.exists(SCHEMA_CACHE):
        with open(SCHEMA_CACHE, 'r') as f:
            return json.load(f)
    
    schema = {}
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        tables = cursor.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
        for (table,) in tables:
            cursor.execute(f'PRAGMA table_info("{table}");')
            columns = cursor.fetchall()
            schema[table] = [{"name": col[1], "type": col[2]} for col in columns]
    
    with open(SCHEMA_CACHE, 'w') as f:
        json.dump(schema, f, indent=2)
    
    return schema

@app.route('/', methods=['GET', 'POST'])
def index():
    query = ""
    result_html = ""
    sql_code = ""
    explanation = ""

    if request.method == 'POST':
        query = request.form['query']
        schema = load_db_schema()

        # Format schema as string for Gemini
        schema_description = "\n".join(
            f"Table: {table}\n" + "\n".join([f" - {col['name']}: {col['type']}" for col in columns])
            for table, columns in schema.items()
        )

        prompt = f"""
        You are a data analyst working with a SQLite database.

        Here is the database schema:
        {schema_description}

        Your task is to:
        1. Convert the user's natural language query into a valid SQLite SQL query.
        2. Use only the table and column names **exactly as provided** in the schema above. Do not rename or modify them. Do not add or remove underscores or alter spacing.
        3. For any text-based filters (such as names, categories, locations, etc.), use case-insensitive and partial matching using the pattern: LOWER(column_name) LIKE '%value%'.
        4. If the query implies excluding similar but different values, use NOT LIKE appropriately to filter them out.
        5. Return only the SQL query inside a code block formatted as ```sql ... ```
        6. After the code block, provide a brief, one-sentence natural language explanation of what the result means.

        User query:
        {query}
        """

        try:
            # Generate SQL + initial explanation
            response = model.generate_content(prompt)
            sql_code, _ = extract_sql(response.text)

            # Run SQL query
            with sqlite3.connect(DB_PATH) as conn:
                df = pd.read_sql_query(sql_code, conn)
                result_html = df.to_html(index=False)

            # Prepare prompt to get natural language explanation based on query output
            explanation_prompt = f"""
            Given the following user question:

            {query}

            And the following SQL query result:

            {df.to_string(index=False)}

            Generate a short, human-readable answer in plain English.
            Avoid technical jargon or SQL syntax — just give a natural explanation based on the numbers, like:
            "There are 100 unique brokers" or "The average investment is ₹10,000."

            Only include the answer sentence. Do not add any extra explanation.
            """

            # Get natural language explanation from Gemini
            explanation_response = model.generate_content(explanation_prompt)
            explanation = explanation_response.text.strip()

        except Exception as e:
            explanation = f"<p style='color:red;'>❌ Error: {e}</p>"

    return render_template("index.html", query=query, result_html=result_html, code=sql_code, explanation=explanation)

if __name__ == '__main__':
    app.run(debug=True)
