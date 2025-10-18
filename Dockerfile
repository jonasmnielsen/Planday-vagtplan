# Brug Python 3.10 som base
FROM python:3.10

# Sæt arbejdsmappe
WORKDIR /app

# Kopiér filer til containeren
COPY . .

# Installer afhængigheder
RUN pip install --no-cache-dir -r requirements.txt

# Start botten
CMD ["python", "bot.py"]
