import sqlite3

con = sqlite3.connect("logs.db")
cur = con.cursor()
for row in cur.execute('SELECT * FROM monitoring_logs;'):
  print(row)

con.close()