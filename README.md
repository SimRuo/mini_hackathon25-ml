**Simon Ruotsalainen**

https://github.com/SimRuo

https://www.linkedin.com/in/simon-ruotsalainen-984ba728a/

**Anton Wiberg**

https://github.com/AntonWiberg1

https://www.linkedin.com/in/anton-wiberg-06223328b/


## server

här ligger Flask backenden som är skriven i python. Databasen är SQLite

app.py -> Huvudfilen 'flask run' för att starta. flask init_db för att skapa databasfilen

schema.sql -> beskriver relationsdatabasen

backfill_delays.py -> hämtar senaste veckans data och skapar en egen regression.db fil som går att träna en modell på

train_delay_modeling.ipynb -> använder regression.db för att träna och spara ner en återanvändbar modell för att förutspå hur försenad ett tåg blir

requirements.txt bör ha alla pip packet du behöver men det kan saknas något. Om du inte har scikit-learn kommer modellen inte funka och du kommer inte få ett error eftersom det ligger i en .joblib fil.

## trafikverket-react

Här ligger frontenden skriven i react. Kopplar till backenden med SSE för att fylla komponenterna med innehåll.

För att komma igång:

```

cd trafikverket-react
npm install
npm run dev

```
