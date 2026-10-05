"""Synthetic drive for the bucket loop: made-up names only, no personal data, fully deterministic.

    python tests/make_synthetic_drive.py            # writes fixtures/synthetic/synthetic_scan.csv and synthetic_truth.csv

synthetic_scan.csv   what the tool sees (path, name, type), like drive_scan.csv
synthetic_truth.csv  the JUDGE: the same files fully sorted as they should be (path, bucket, tag)
                     bucket = the correct bucket, "Unsorted_Miscellaneous" when the name and path hold no usable
                     information (refusing is the right answer), "_Software_Projects" / "_Temp_Safe_To_Remove"
                     for files the fixed rules must take out before any bucket is chosen.
                     tag = the difficulty the file belongs to, so results can be read per situation.

Hard situations on purpose: folders whose name hides what is inside, mixed dump folders, a minority of files in
the wrong kind of folder (decoys), software packages full of documents and scripts, generic photo names,
names with no information, overlapping topics, deep trees with clean children, transliterated names.
"""
import csv
import os
import random

BS = "\\"
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fixtures", "synthetic")
UNS = "Unsorted_Miscellaneous"
BUCKETS = ["Study_Engineering", "Programming_Courses", "Software_Installers", "Finance_Tax_Bills", "Identity_Legal", "Family_Photos",
           "Music", "Movies_Shows", "Books_Ebooks", "Work_Projects", "Health_Medical", "Travel"]
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
STUDY = ["Soil Mechanics", "Fluid Mechanics", "Structural Analysis", "Concrete Technology", "Surveying", "Highway Engineering",
         "Hydrology", "Steel Design", "Earthquake Engineering", "Foundation Engineering"]
CODE = ["Python Basics", "Pandas Data Analysis", "JavaScript Essentials", "SQL Masterclass", "Git and GitHub", "Django Web Development",
        "Machine Learning Crash Course"]
PACKAGES = ["VLC 3.0.18", "7-Zip 22.01", "Notepad++ 8.4", "Adobe Reader DC", "Office 2019 Setup", "Chrome Offline Installer",
            "WinRAR 6.2", "PyCharm 2022.3", "Audacity 3.2", "Python 3.11.4 x64"]
ARTISTS = ["The Midnight Owls", "Rhea Kapoor", "Blue River Band", "Anand Sagar", "Luna Park", "Iron Tides", "Meera Joshi", "Static Garden",
           "Paper Kites Orchestra", "Dev Malhotra", "Northern Lights Trio", "Saffron Road", "Velvet Hours", "Kabir Das Ensemble", "Echo Valley"]
TITLES = ["Midnight Train", "Golden Hour", "Paper Boats", "Slow Rain", "Ocean Drive", "Red Lanterns", "Wild Roses", "Silver Lining", "Hollow Moon",
          "Last Summer", "Quiet Storm", "Neon Streets", "Winter Light", "Open Road", "Broken Compass", "Sunday Morning", "Falling Leaves", "Glass House"]
MOVIES = ["The Last Harbor", "Crimson Valley", "Midnight Express 2", "A Quiet Revolution", "Desert Kings", "The Glass Garden", "Iron Skies",
          "Monsoon Letters", "Parallel Hearts", "The Long Way Home", "Silent Witness", "Paper Moon Rising"]
SHOWS = ["Harbor Lights", "The Bureau", "City of Glass", "Nine Lives", "Summer Camp Chronicles"]
AUTHORS = ["Arundhati Vale", "Peter Holloway", "Sunita Rao", "Marcus Bell", "Nadia Farouk", "Tom Ellery", "Priya Nair", "Hugo Brandt", "Lena Marsh",
           "Omar Khalid", "Ruth Calloway", "Vikram Sethi"]
BOOKS = ["The Salt Road", "Letters from Kerala", "A Brief History of Rivers", "The Clockmaker's Daughter", "Nine Winters", "Quiet Engines",
         "The Orchard Keeper", "Maps of Nowhere", "Under the Banyan", "The Last Telegram", "Bridges and Bones", "Understanding Statistics"]
CLIENTS = ["Greenfield Developers", "Apex Infra", "Sunrise Builders", "Metro Rail Corp", "Harbor Logistics"]
CITIES = ["Goa", "Jaipur", "Singapore", "Kochi", "Manali", "Dubai", "Lisbon", "Shimla"]
EVENTS = ["Birthday Party", "Diwali", "Wedding", "Goa Trip", "School Annual Day", "Summer Holidays", "Family Reunion"]
PEOPLE = ["Asha", "Ravi", "Meera"]
DOCTORS = ["Dr Mehta", "Dr Sharma", "Dr Iyer", "Dr Banerjee"]
WEAK = ["scan{n:04d}.pdf", "document ({n}).pdf", "{n:07d}.pdf", "New Text Document.txt", "Untitled{n}.docx", "file_{n}.dat", "image{n:03d}.png",
        "download ({n}).pdf", "New Microsoft Word Document ({n}).docx", "Copy of Copy ({n}).pdf"]


def build(seed=7):
    # Each block below is one hard situation and sets its own tag, so a weak spot shows up per situation in the judge's report.
    # Truth is decided here, by the scenario that created the file; a file with no usable information is truth "Unsorted_Miscellaneous".
    rng = random.Random(seed)
    rows = []

    def add(folder, name, bucket, tag):
        rows.append(((folder + BS + name) if folder else name, bucket, tag))

    def yr(): return rng.choice([2017, 2018, 2019, 2020, 2021, 2022, 2023])
    def month(): return rng.choice(MONTHS)

    # one descriptive file for a bucket (used by dump folders and decoys)
    def descriptive(bucket):
        if bucket == "Finance_Tax_Bills":
            return rng.choice([f"Electricity Bill {month()} {yr()}.pdf", f"Bank Statement {month()} {yr()}.pdf", f"Form 16 {yr()}.pdf",
                               f"ITR Acknowledgement {yr()}.pdf", f"Credit Card Statement {month()} {yr()}.pdf", f"Rent Receipt {month()} {yr()}.pdf"])
        if bucket == "Identity_Legal":
            return rng.choice(["Passport Scan.pdf", "Aadhaar Card Front.jpg", "PAN Card.pdf", "Driving Licence.jpg", "Rental Agreement 2021.pdf",
                               "Property Sale Deed.pdf", "Voter ID.jpg", "Marriage Certificate.pdf"])
        if bucket == "Health_Medical":
            return rng.choice([f"Blood Test Report {month()} {yr()}.pdf", f"Prescription {rng.choice(DOCTORS)}.jpg", "X-Ray Chest.jpg",
                               f"Insurance Claim {rng.randint(100, 999)}.pdf", "Vaccination Certificate.pdf", f"Dental Invoice {yr()}.pdf"])
        if bucket == "Travel":
            c = rng.choice(CITIES)
            return rng.choice([f"Flight Ticket Delhi to {c}.pdf", f"Hotel Booking {c}.pdf", f"Itinerary {c} {yr()}.docx", f"Boarding Pass {rng.randint(10, 99)}.pdf", f"Train Ticket Pune to {c}.pdf"])
        if bucket == "Work_Projects":
            return rng.choice([f"Project {rng.randint(100, 999)} Status Report {month()}.docx", f"Client Proposal {rng.choice(CLIENTS)}.pptx",
                               f"Meeting Minutes {month()} {yr()}.docx", f"BOQ {rng.randint(100, 999)}.xlsx", f"Site Visit Notes {month()}.docx"])
        if bucket == "Study_Engineering":
            return f"{rng.choice(STUDY)} Notes Unit {rng.randint(1, 6)}.pdf"
        if bucket == "Programming_Courses":
            return f"{rng.choice(CODE)} - Lesson {rng.randint(1, 30)}.mp4"
        if bucket == "Software_Installers":
            return rng.choice(["vlc-3.0.18-win64.exe", "7z2201-x64.msi", "npp.8.4.Installer.exe", "ChromeSetup.exe", "winrar-x64-621.exe", "audacity-win-3.2.exe"])
        if bucket == "Music":
            return f"{rng.choice(ARTISTS)} - {rng.choice(TITLES)}.mp3"
        if bucket == "Movies_Shows":
            return f"{rng.choice(MOVIES)} ({rng.randint(2005, 2023)}) 720p.mkv"
        if bucket == "Books_Ebooks":
            return f"{rng.choice(AUTHORS)} - {rng.choice(BOOKS)}.epub"
        if bucket == "Family_Photos":
            return f"IMG_{rng.randint(1, 9999):04d}.jpg"
        raise ValueError(bucket)

    # ---- S1 clean, well-named folders -------------------------------------------------------------------------
    for t in STUDY:
        for n in range(1, 31): add(f"Lectures{BS}NPTEL{BS}{t}", f"Mod-{(n - 1) // 6 + 1:02d} Lec-{n:02d} {t}.mp4", "Study_Engineering", "clean")
        for k in range(1, 7): add(f"Lectures{BS}NPTEL{BS}{t}", f"{t} Notes Unit {k}.pdf", "Study_Engineering", "clean")
    for t in CODE:
        for n in range(1, 31):
            add(f"Courses{BS}Udemy{BS}{t}", f"{t} - Lesson {n}.mp4", "Programming_Courses", "clean")
            add(f"Courses{BS}Udemy{BS}{t}", f"{t} - Lesson {n}.srt", "Programming_Courses", "clean")
        for k in range(1, 9): add(f"Courses{BS}Udemy{BS}{t}{BS}exercises", f"exercise_{k:02d}.py", "Programming_Courses", "clean")
        add(f"Courses{BS}Udemy{BS}{t}", f"{t} slides.pdf", "Programming_Courses", "clean")
    for m in MOVIES:
        y = rng.randint(2005, 2023)
        for name in (f"{m} ({y}) 1080p.mkv", f"{m} ({y}).en.srt", "poster.jpg"): add(f"Movies{BS}{m} ({y})", name, "Movies_Shows", "clean")
    for s in SHOWS:
        for season in (1, 2):
            for e in range(1, 9): add(f"Shows{BS}{s}{BS}Season {season}", f"{s} S{season:02d}E{e:02d}.mp4", "Movies_Shows", "clean")
    for i, artist in enumerate(ARTISTS):
        album = f"{TITLES[i % len(TITLES)]} ({yr()})"
        for n in range(1, 13): add(f"Music{BS}{artist}{BS}{album}", f"{n:02d} - {rng.choice(TITLES)} {n}.mp3", "Music", "clean")
        for name in ("cover.jpg", "folder.jpg"): add(f"Music{BS}{artist}{BS}{album}", name, "Music", "clean")
    for i, b in enumerate(BOOKS):
        a = AUTHORS[i % len(AUTHORS)]
        for name in (f"{b} - {a}.epub", "cover.jpg", "metadata.opf"): add(f"Books{BS}{a}{BS}{b}", name, "Books_Ebooks", "clean")
    for i in range(40): add(f"Books{BS}Technical", f"{rng.choice(AUTHORS)} - {rng.choice(BOOKS)} {i}.pdf", "Books_Ebooks", "clean")

    # ---- S2 software packages full of documents, scripts and pictures --------------------------------------------
    for pkg in PACKAGES:
        base = f"Software{BS}{pkg}"
        for name in ("setup.exe", "uninstall.exe", "config.xml", "license.txt", "readme.txt", "changelog.txt", "manual.pdf"): add(base, name, "Software_Installers", "installer-package")
        for n in range(1, 24): add(f"{base}{BS}libs", f"{rng.choice(['avcodec', 'msvcp', 'qt5core', 'libssl', 'zlib', 'icu'])}-{n}.dll", "Software_Installers", "installer-package")
        for lang in ["en-US", "de-DE", "fr-FR", "es-ES", "hi-IN", "ja-JP", "pt-BR", "ru-RU"]: add(f"{base}{BS}locales", f"{lang}.pak", "Software_Installers", "installer-package")
        for n in range(1, 12): add(f"{base}{BS}resources", f"icon_{n}.png", "Software_Installers", "installer-package")
        if pkg.startswith("Python"):
            for n in range(1, 41): add(f"{base}{BS}Lib", rng.choice(["os", "json", "re", "sys", "typing", "pathlib"]) + f"_{n}.py", "Software_Installers", "installer-package")

    # ---- S3 photos with generic names; the folder says what they are --------------------------------------------
    for ev in EVENTS:
        for year in (2019, 2022):
            for n in range(35):
                pick = rng.choice([f"IMG_{rng.randint(1, 9999):04d}.jpg", f"DSC{rng.randint(1, 99999):05d}.jpg", f"PXL_{year}{rng.randint(1, 12):02d}{rng.randint(1, 28):02d}_{rng.randint(100000, 235959)}.jpg",
                                   f"VID_{year}{rng.randint(1, 12):02d}{rng.randint(1, 28):02d}_{rng.randint(100000, 235959)}.mp4"])
                add(f"Photos{BS}{ev} {year}", pick, "Family_Photos", "clean-generic")

    # ---- S4 small clean folders (too small to vote) --------------------------------------------------------------
    for year in range(2017, 2024):
        for n in range(14): add(f"Finance{BS}Bills{BS}{year}", rng.choice([f"Electricity Bill {month()} {year}.pdf", f"Water Bill {month()} {year}.pdf", f"Credit Card Statement {month()} {year}.pdf"]) if n % 3 else f"Bank Statement {MONTHS[n % 12]} {year}.pdf", "Finance_Tax_Bills", "small-clean")
        for name in (f"Form 16 {year}.pdf", f"ITR Acknowledgement {year}.pdf", f"Tax Computation {year}.xlsx", f"LIC Premium Receipt {year}.pdf"): add(f"Tax{BS}{year}", name, "Finance_Tax_Bills", "small-clean")
    for name in ("Passport Scan.pdf", "Aadhaar Card Front.jpg", "Aadhaar Card Back.jpg", "PAN Card.pdf", "Driving Licence.jpg", "Voter ID.jpg", "Marriage Certificate.pdf", "Birth Certificate.pdf"): add(f"Documents{BS}Identity", name, "Identity_Legal", "small-clean")
    for name in ("Rental Agreement 2021.pdf", "Property Sale Deed.pdf", "Will Draft.docx", "Power of Attorney.pdf", "Affidavit 1.pdf", "Affidavit 2.pdf"): add("Legal", name, "Identity_Legal", "small-clean")
    for p in PEOPLE:
        for name in (f"Blood Test Report {month()} 2022.pdf", f"Prescription {rng.choice(DOCTORS)}.jpg", f"Prescription {rng.choice(DOCTORS)} 2.jpg", "X-Ray Chest.jpg", "Vaccination Certificate.pdf", f"Insurance Claim {rng.randint(100, 999)}.pdf", f"Dental Invoice {yr()}.pdf", f"ECG Report {yr()}.pdf"):
            add(f"Health{BS}{p}", name, "Health_Medical", "small-clean")
    for c in CITIES[:6]:
        year = yr()
        for name in (f"Flight Ticket Delhi to {c}.pdf", f"Hotel Booking {c}.pdf", f"Itinerary {c} {year}.docx", f"Boarding Pass {rng.randint(10, 99)}.pdf", f"Travel Insurance {c}.pdf"): add(f"Travel{BS}{c} {year}", name, "Travel", "small-clean")
    add(f"Travel{BS}Goa Trip 2019", "Hotel Booking Goa.pdf", "Travel", "overlap")      # same folder name as a photo folder
    add(f"Travel{BS}Goa Trip 2019", "Flight Ticket Delhi to Goa.pdf", "Travel", "overlap")

    # ---- S5 dump folders with descriptive names (mixed on purpose) -----------------------------------------------
    for dump, count in (("Downloads", 200), ("Desktop", 100)):
        for _ in range(count):
            b = rng.choice(BUCKETS)
            add(dump, descriptive(b), b, "dump-descriptive")

    # ---- S6 names that carry no information: refusing is the right answer ------------------------------------------
    for i in range(60):
        add(rng.choice(["New Folder (2)", "Misc", "Old Stuff"]), rng.choice(WEAK).format(n=rng.randint(1, 9999)), UNS, "unknowable")

    # ---- S7 decoys: a minority of files in the wrong kind of folder --------------------------------------------------
    for n in range(120): add(f"Music{BS}Old Collection", f"{rng.choice(ARTISTS)} - {rng.choice(TITLES)} {n}.mp3", "Music", "decoy-host")
    for b in ("Finance_Tax_Bills", "Travel", "Health_Medical", "Identity_Legal", "Work_Projects") * 5: add(f"Music{BS}Old Collection", descriptive(b), b, "decoy-minority")
    for n in range(100): add(f"Movies{BS}Weekend Watch", f"{rng.choice(MOVIES)} ({rng.randint(2005, 2023)}) part {n}.mkv", "Movies_Shows", "decoy-host")
    for b in ("Study_Engineering", "Programming_Courses", "Books_Ebooks", "Finance_Tax_Bills") * 5: add(f"Movies{BS}Weekend Watch", descriptive(b), b, "decoy-minority")

    # ---- S8 overlapping topics ------------------------------------------------------------------------------------
    for client in CLIENTS:
        code = rng.randint(100, 999)
        for n in range(1, 5): add(f"Work{BS}{client}{BS}{code}", f"Invoice {client} {n}.pdf", "Work_Projects", "overlap")
        for name in (f"Project {code} Status Report {month()}.docx", f"Drawing {code} Rev {rng.randint(1, 4)}.pdf", f"BOQ {code}.xlsx", f"Site Visit Notes {month()}.docx", f"Meeting Minutes {month()}.docx", f"Client Proposal {client}.pptx"):
            add(f"Work{BS}{client}{BS}{code}", name, "Work_Projects", "overlap")
    for n in range(1, 26): add(f"Courses{BS}Python for Civil Engineers", f"Python for Civil Engineers - Lesson {n}.mp4", "Programming_Courses", "overlap")
    for c in CITIES[:3]: add(f"Travel{BS}{c} 2023", f"Travel Expenses {c}.xlsx", "Travel", "overlap")

    # ---- S9 deep tree: a mixed parent with clean children ---------------------------------------------------------
    root = f"Backup{BS}Old Laptop 2017{BS}Documents"
    for n in range(40): add(f"{root}{BS}Study", f"{rng.choice(STUDY)} Notes Unit {n % 6 + 1} copy{n}.pdf", "Study_Engineering", "deep-clean-children")
    for n in range(40): add(f"{root}{BS}Pictures", f"IMG_{rng.randint(1, 9999):04d}.jpg", "Family_Photos", "deep-clean-children")
    for n in range(40): add(f"{root}{BS}Songs", f"{rng.choice(ARTISTS)} - {rng.choice(TITLES)} {n}.mp3", "Music", "deep-clean-children")
    for n in range(30): add(f"{root}{BS}Bills", f"Electricity Bill {month()} {yr()} {n}.pdf", "Finance_Tax_Bills", "deep-clean-children")

    # ---- S10 transliterated names ---------------------------------------------------------------------------------
    for n in range(1, 61):
        add(f"Audio{BS}Sacred", rng.choice([f"Hanuman Chalisa {n}.mp3", f"Bhajan Sangrah {n:02d}.mp3", f"Aarti Collection {n}.mp3", f"Kirtan Evening {n}.mp3", f"Ghazal Mehfil {n}.mp3"]), "Music", "transliterated")

    # ---- S11 fixed rules must take these out before any bucket is chosen -------------------------------------------
    for n in range(40): add(f"Projects{BS}website{BS}.git{BS}objects{BS}{n % 16:02x}", f"{rng.getrandbits(40):010x}", "_Software_Projects", "rule-unit")
    for n in range(1, 21): add(f"Projects{BS}website", f"page{n}.html", "_Software_Projects", "rule-unit")
    for n in range(60): add(f"Projects{BS}api{BS}.venv{BS}Lib{BS}site-packages{BS}pkg{n}", f"mod{n}.py", "_Software_Projects", "rule-unit")
    for n in range(30): add(rng.choice(["Documents", "Desktop"]), rng.choice([f"~$report{n}.docx", f"cache{n}.tmp", "Thumbs.db"]) if n % 3 else f"scratch{n}.tmp", "_Temp_Safe_To_Remove", "rule-junk")

    return rows


def write(out_dir=OUT, seed=7):
    os.makedirs(out_dir, exist_ok=True)
    rows = build(seed)
    seen = set()
    unique = []
    for path, bucket, tag in rows:                      # a path must exist once; keep the first
        if path in seen:
            continue
        seen.add(path)
        unique.append((path, bucket, tag))
    with open(os.path.join(out_dir, "synthetic_scan.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["path", "name", "type"])
        for path, _, _ in unique:
            name, ext = os.path.splitext(path.split(BS)[-1])
            w.writerow([path, name, ext.lower()])
    with open(os.path.join(out_dir, "synthetic_truth.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["path", "bucket", "tag"])
        w.writerows(unique)
    return unique


if __name__ == "__main__":
    unique = write()
    import collections
    tags = collections.Counter(t for _, _, t in unique)
    print(f"{len(unique)} files written to {OUT}")
    for tag, n in tags.most_common(): print(f"  {n:5d}  {tag}")
