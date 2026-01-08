#!/bin/bash
# PostgreSQL Setup Script
# Testet og udført på AlmaLinux 10 (Fedora baseret)

set -e  # Stop ved fejl

echo "==================================="
echo "PostgreSQL Setup Script"
echo "==================================="
echo

# Variabler vi skal bruge - kan ændres undtagen PG_HBA, som er PostgreSQL's sikkerhedsfil.
# HBA = Host based Authentication
DB_USER="remote_server_admin"
DB_PASSWORD="RemoteServer"
DB_NAME="udluftningssystem_database"
PG_HBA="/var/lib/pgsql/data/pg_hba.conf"

# Track hvad vi har lavet for rollback
POSTGRES_INSTALLED=false
POSTGRES_ENABLED=false
POSTGRES_INITIALIZED=false
POSTGRES_STARTED=false
DB_CREATED=false
PG_HBA_MODIFIED=false

# Cleanup funktion ved fejl - dette gør så vi tjekker hvilke steps vi fik gennemført og derefter
# sletter det hele, så vi ikke har ufærdige filer og installationer liggende
cleanup_on_error() {
    echo "Fejl opstået - oprydning starter"
    
    if [ "$DB_CREATED" = true ]; then
        echo " Sletter database og user"
        sudo -u postgres psql -c "DROP DATABASE IF EXISTS $DB_NAME;" 2>/dev/null || true
        sudo -u postgres psql -c "DROP USER IF EXISTS $DB_USER;" 2>/dev/null || true
    fi
    
    if [ "$PG_HBA_MODIFIED" = true ] && [ -f "${PG_HBA}.backup" ]; then
        echo "Gendanner pg_hba.conf fra backup"
        sudo cp ${PG_HBA}.backup $PG_HBA
        sudo rm -f ${PG_HBA}.backup
    fi
    
    if [ "$POSTGRES_STARTED" = true ]; then
        echo "Stopper PostgreSQL"
        sudo systemctl stop postgresql 2>/dev/null || true
    fi
    
    if [ "$POSTGRES_ENABLED" = true ]; then
        echo "Annullerer PostgreSQL autostart"
        sudo systemctl disable postgresql 2>/dev/null || true
    fi
    
    if [ "$POSTGRES_INITIALIZED" = true ]; then
        echo "Fjerner database cluster"
        sudo rm -rf /var/lib/pgsql/data 2>/dev/null || true
    fi
    
    if [ "$POSTGRES_INSTALLED" = true ]; then
        echo "Afinstallerer PostgreSQL"
        sudo dnf remove -y postgresql-server postgresql-contrib postgresql postgresql-private-libs uuid 2>/dev/null || true
    fi
    
    echo
    echo "Rollback komplet, script slutter uden installation og opsætning af PostgreSQL database"
    exit 1
}

# Sæt trap til at fange fejl
trap cleanup_on_error ERR

# 1. step - Installer PostgreSQL
echo "Installerer PostgreSQL"
sudo dnf install -y postgresql-server postgresql-contrib
POSTGRES_INSTALLED=true

# 2. step aktiver PostgreSQL service
echo "Aktiverer (enable) PostgreSQL service"
sudo systemctl enable postgresql
POSTGRES_ENABLED=true

# 3. step - Initialiser database
echo "Initialiserer database"
sudo postgresql-setup --initdb --unit postgresql
POSTGRES_INITIALIZED=true

# 4. step - Start PostgreSQL
echo "Starter PostgreSQL"
sudo systemctl start postgresql
POSTGRES_STARTED=true

# 5. step - Opret database user og database
echo "Opretter user og database"
sudo -u postgres psql << EOF
CREATE USER $DB_USER WITH PASSWORD '$DB_PASSWORD';
CREATE DATABASE $DB_NAME OWNER $DB_USER;
GRANT ALL PRIVILEGES ON DATABASE $DB_NAME TO $DB_USER;
\q
EOF
DB_CREATED=true

# 6. step - Ændrer pg_hba.conf for at tillade password authentication
echo "Konfigurerer HBA"

# Backup original fil
sudo cp $PG_HBA ${PG_HBA}.backup
PG_HBA_MODIFIED=true

# Tilføj local regel (før existing "local all all peer")
# sed giver os mulighed for at skrive i en fil ved at give et "regex"-mønster at gå ud fra.
# -i betyder "in-place" og gør altså at vi skriver ud i filen - specifikt indsætter vi vores tekst
# på linjen før den vi leder efter da vi afslutter med /i
# Regex mønsteret fungerer således: ^ = Start af linjen. \*s betyder at der skal være mellemrum.
# og tekst som f.eks. local, er så den tekst vi leder efter.
# Vi leder altså efter linjen: local	all	all	peer
# Vi tager så og skriver vores linje ind på linjen før den vi fandt
sudo sed -i "/^local\s*all\s*all\s*peer/i local   $DB_NAME      $DB_USER     md5" $PG_HBA

# Tilføj IPv4 regel (før existing "host all all 127.0.0.1/32 ident")
# Samme fremgang som før
sudo sed -i "/^host\s*all\s*all\s*127.0.0.1\/32\s*ident/i host    $DB_NAME      $DB_USER     127.0.0.1/32    md5" $PG_HBA

# Tilføj IPv6 regel (før existing "host all all ::1/128 ident")
# Samme fremgang som før
sudo sed -i "/^host\s*all\s*all\s*::1\/128\s*ident/i host    $DB_NAME      $DB_USER     ::1/128         md5" $PG_HBA

# 7. step - Genstart PostgreSQL
echo "Genstarter PostgreSQL"
sudo systemctl restart postgresql

# 8. step - Test forbindelsen
echo "Tester forbindelse til PostgreSQL"
echo "Forsøger at forbinde"
PGPASSWORD=$DB_PASSWORD psql -U $DB_USER -d $DB_NAME -h localhost -c "SELECT version();" > /dev/null 2>&1

if [ $? -eq 0 ]; then
    # Hvis det lykkedes fjerner vi vores error trap, så vi ikke længere leder efter fejl.
    trap - ERR
    
    echo "Fuldført! PostgreSQL database er klar"
    echo "Backup er gemt"
else
    echo "Fejl: Kunne ikke forbinde til databasen"
    echo "Check logs med: sudo journalctl -u postgresql -n 50"
    exit 1
fi

echo "Setup komplet"