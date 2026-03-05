from flask import Flask, render_template, request, jsonify
import requests
import time
import sqlite3
import json
import os # YENİ
from dotenv import load_dotenv # YENİ

# .env dosyasındaki gizli bilgileri okur
load_dotenv() 

app = Flask(__name__)

# API anahtarını artık koddan değil, güvenli .env dosyasından çekiyoruz!
API_KEY = os.getenv("RIOT_API_KEY") 

if not API_KEY:
    raise ValueError("KRİTİK HATA: .env dosyasında RIOT_API_KEY bulunamadı!")

CACHE_TTL_PUUID = 30 * 24 * 60 * 60 
CACHE_TTL_RANK = 15 * 60            
CACHE_TTL_MATCH = 2 * 60 
CACHE_TTL_MASTERY = 24 * 60 * 60 
CACHE_TTL_KDA = 15 * 60 # KDA verileri 15 dakika hafızada kalsın

QUEUE_MAP = {
    420: "Dereceli (Tek/Çift)",
    440: "Dereceli (Esnek)",
    400: "Sıralı Seçim",
    430: "Kapalı Seçim",
    450: "ARAM",
    490: "Hızlı Oyun",
    700: "Clash"
}

def init_db():
    with sqlite3.connect("lol_cache.db") as conn:
        c = conn.cursor()
        c.execute("CREATE TABLE IF NOT EXISTS puuid_cache (riot_id TEXT PRIMARY KEY, puuid TEXT, timestamp REAL)")
        c.execute("CREATE TABLE IF NOT EXISTS riot_id_cache (puuid TEXT PRIMARY KEY, riot_id TEXT, timestamp REAL)")
        c.execute("CREATE TABLE IF NOT EXISTS rank_cache (puuid TEXT PRIMARY KEY, rank_data TEXT, timestamp REAL)")
        c.execute("CREATE TABLE IF NOT EXISTS match_cache (puuid TEXT PRIMARY KEY, match_data TEXT, timestamp REAL)")
        c.execute("CREATE TABLE IF NOT EXISTS mastery_cache (puuid_champ TEXT PRIMARY KEY, points TEXT, timestamp REAL)")
        c.execute("CREATE TABLE IF NOT EXISTS kda_cache (puuid TEXT PRIMARY KEY, kda_data TEXT, timestamp REAL)") # YENİ: KDA Tablosu
        conn.commit()

init_db()

def get_ddragon_data():
    versions_url = "https://ddragon.leagueoflegends.com/api/versions.json"
    latest_version = requests.get(versions_url).json()[0]
    
    champ_url = f"https://ddragon.leagueoflegends.com/cdn/{latest_version}/data/tr_TR/champion.json"
    champ_data = requests.get(champ_url).json()
    champ_dict = {
        int(info['key']): {
            'name': info['name'],
            'icon': f"https://ddragon.leagueoflegends.com/cdn/{latest_version}/img/champion/{info['id']}.png"
        } for name, info in champ_data['data'].items()
    }

    spell_url = f"https://ddragon.leagueoflegends.com/cdn/{latest_version}/data/tr_TR/summoner.json"
    spell_data = requests.get(spell_url).json()
    spell_dict = {
        int(info['key']): f"https://ddragon.leagueoflegends.com/cdn/{latest_version}/img/spell/{info['id']}.png"
        for name, info in spell_data['data'].items()
    }

    rune_url = f"https://ddragon.leagueoflegends.com/cdn/{latest_version}/data/tr_TR/runesReforged.json"
    rune_data = requests.get(rune_url).json()
    rune_dict, tree_dict = {}, {}
    for tree in rune_data:
        tree_dict[tree['id']] = f"https://ddragon.leagueoflegends.com/cdn/img/{tree['icon']}"
        for slot in tree['slots']:
            for rune in slot['runes']:
                rune_dict[rune['id']] = f"https://ddragon.leagueoflegends.com/cdn/img/{rune['icon']}"
    return champ_dict, spell_dict, rune_dict, tree_dict

CHAMPS, SPELLS, RUNES, TREES = get_ddragon_data()

def get_puuid(game_name, tag_line, api_key):
    riot_id_key = f"{game_name}#{tag_line}".lower()
    with sqlite3.connect("lol_cache.db") as conn:
        c = conn.cursor()
        c.execute("SELECT puuid, timestamp FROM puuid_cache WHERE riot_id=?", (riot_id_key,))
        row = c.fetchone()
        if row and (time.time() - row[1]) < CACHE_TTL_PUUID: return row[0]
            
    url = f"https://europe.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{game_name}/{tag_line}"
    response = requests.get(url, headers={"X-Riot-Token": api_key})
    if response.status_code == 200:
        puuid = response.json().get('puuid')
        with sqlite3.connect("lol_cache.db") as conn:
            conn.cursor().execute("REPLACE INTO puuid_cache (riot_id, puuid, timestamp) VALUES (?, ?, ?)", (riot_id_key, puuid, time.time()))
            conn.commit()
        return puuid
    return None

def get_riot_id_by_puuid(puuid, api_key):
    if not puuid: return "Bilinmeyen"
    with sqlite3.connect("lol_cache.db") as conn:
        c = conn.cursor()
        c.execute("SELECT riot_id, timestamp FROM riot_id_cache WHERE puuid=?", (puuid,))
        row = c.fetchone()
        if row and (time.time() - row[1]) < CACHE_TTL_PUUID: return row[0]
            
    url = f"https://europe.api.riotgames.com/riot/account/v1/accounts/by-puuid/{puuid}"
    response = requests.get(url, headers={"X-Riot-Token": api_key})
    if response.status_code == 200:
        data = response.json()
        riot_id = f"{data.get('gameName')}#{data.get('tagLine')}"
        with sqlite3.connect("lol_cache.db") as conn:
            conn.cursor().execute("REPLACE INTO riot_id_cache (puuid, riot_id, timestamp) VALUES (?, ?, ?)", (puuid, riot_id, time.time()))
            conn.commit()
        return riot_id
    return "Gizli Oyuncu"

def get_rank_info(puuid, api_key, force=False):
    default_rank = {"text": "Derecesiz", "color_class": "unranked", "icon": "", "wr": 0, "total": 0, "hot_streak": False}
    if not puuid: return default_rank
    
    if not force:
        with sqlite3.connect("lol_cache.db") as conn:
            c = conn.cursor()
            c.execute("SELECT rank_data, timestamp FROM rank_cache WHERE puuid=?", (puuid,))
            row = c.fetchone()
            if row and (time.time() - row[1]) < CACHE_TTL_RANK:
                return json.loads(row[0])
            
    url = f"https://tr1.api.riotgames.com/lol/league/v4/entries/by-puuid/{puuid}"
    response = requests.get(url, headers={"X-Riot-Token": api_key})
    
    rank_data = default_rank
    if response.status_code == 200:
        data = response.json()
        for league in data:
            q_type = league.get('queueType')
            tier = league.get('tier', '').capitalize()
            rank = league.get('rank', '')
            wins, losses = league.get('wins', 0), league.get('losses', 0)
            total = wins + losses
            wr = int((wins / total) * 100) if total > 0 else 0
            color_class = tier.lower() if tier else "unranked"
            icon_url = f"https://raw.communitydragon.org/latest/plugins/rcp-fe-lol-shared-components/global/default/{color_class}.png" if color_class != "unranked" else ""
            hot_streak = league.get('hotStreak', False) # YENİ: Alevli Seri Verisi
            
            if q_type == "RANKED_SOLO_5x5":
                rank_data = {"text": f"{tier} {rank} (%{wr} WR - {total} Maç)", "color_class": color_class, "icon": icon_url, "wr": wr, "total": total, "hot_streak": hot_streak}
                break
            elif q_type == "RANKED_FLEX_SR" and rank_data["text"] == "Derecesiz":
                rank_data = {"text": f"Flex: {tier} {rank} (%{wr} WR)", "color_class": color_class, "icon": icon_url, "wr": wr, "total": total, "hot_streak": hot_streak}
        
        with sqlite3.connect("lol_cache.db") as conn:
            conn.cursor().execute("REPLACE INTO rank_cache (puuid, rank_data, timestamp) VALUES (?, ?, ?)", (puuid, json.dumps(rank_data), time.time()))
            conn.commit()
        return rank_data
    return default_rank

def get_mastery_info(puuid, champ_id, api_key):
    if not puuid or not champ_id: return {"raw": 0, "text": "0 Ustalık Puanı"}
    
    db_key = f"{puuid}_{champ_id}"
    with sqlite3.connect("lol_cache.db") as conn:
        c = conn.cursor()
        c.execute("SELECT points, timestamp FROM mastery_cache WHERE puuid_champ=?", (db_key,))
        row = c.fetchone()
        if row and (time.time() - row[1]) < CACHE_TTL_MASTERY:
            return json.loads(row[0])
            
    url = f"https://tr1.api.riotgames.com/lol/champion-mastery/v4/champion-masteries/by-puuid/{puuid}/by-champion/{champ_id}"
    response = requests.get(url, headers={"X-Riot-Token": api_key})
    
    pts = 0
    pts_text = "0 Ustalık Puanı"
    if response.status_code == 200:
        pts = response.json().get('championPoints', 0)
        if pts >= 1000000:
            pts_text = f"{pts/1000000:.1f}M Ustalık Puanı"
        elif pts >= 1000:
            pts_text = f"{int(pts/1000)}K Ustalık Puanı"
        else:
            pts_text = f"{pts} Ustalık Puanı"
            
    result = {"raw": pts, "text": pts_text}
    with sqlite3.connect("lol_cache.db") as conn:
        conn.cursor().execute("REPLACE INTO mastery_cache (puuid_champ, points, timestamp) VALUES (?, ?, ?)", (db_key, json.dumps(result), time.time()))
        conn.commit()
    return result

def get_live_match_data(puuid, api_key, force_update=False):
    cooldown_warning = False
    with sqlite3.connect("lol_cache.db") as conn:
        c = conn.cursor()
        c.execute("SELECT match_data, timestamp FROM match_cache WHERE puuid=?", (puuid,))
        row = c.fetchone()
        if force_update and row and (time.time() - row[1]) < CACHE_TTL_MATCH:
            cooldown_warning = True
            return json.loads(row[0]), None, cooldown_warning
        if not force_update and row and (time.time() - row[1]) < CACHE_TTL_MATCH:
            return json.loads(row[0]), None, False

    url = f"https://tr1.api.riotgames.com/lol/spectator/v5/active-games/by-summoner/{puuid}"
    response = requests.get(url, headers={"X-Riot-Token": api_key})
    
    if response.status_code == 200:
        data = response.json()
        queue_id = data.get('gameQueueConfigId')
        gercek_mod = QUEUE_MAP.get(queue_id, data.get('gameMode', 'Bilinmiyor'))
        if data.get('gameType') == "CUSTOM_GAME": gercek_mod = "Özel Oyun / Antrenman"
            
        bans = {"blue": [], "red": []}
        for ban in data.get('bannedChampions', []):
            cid = ban.get('championId')
            if cid > 0:
                c_icon = CHAMPS.get(cid, {}).get('icon', '')
                if c_icon:
                    bans["blue" if ban.get('teamId') == 100 else "red"].append(c_icon)

        match_info = {"gameMode": gercek_mod, "blue_team": [], "red_team": [], "bans": bans}
        
        for p in data.get('participants', []):
            p_puuid = p.get('puuid')
            team_id = p.get('teamId')
            champ_id = p.get('championId')
            c_info = CHAMPS.get(champ_id, {'name': 'Bilinmeyen', 'icon': ''})
            s1_id = p.get('spell1Id')
            s2_id = p.get('spell2Id')
            p_ids = p.get('perks', {}).get('perkIds', [])
            
            rank_info = get_rank_info(p_puuid, api_key, force=force_update)
            mastery_data = get_mastery_info(p_puuid, champ_id, api_key)
            
            # YENİ: Dayı'nın Zeki Kişilik Analizi (Etiketler)
            tags = []
            raw_pts = mastery_data['raw']
            wr = rank_info.get('wr', 0)
            total = rank_info.get('total', 0)
            
            if raw_pts >= 500000:
                tags.append({"text": "OTP 👑", "class": "bg-warning text-dark", "tip": "Bu karakterin hastası!"})
            elif raw_pts < 5000:
                tags.append({"text": "Tek Atımlık 🎯", "class": "bg-secondary text-light", "tip": "Bu şampiyonda çok tecrübesiz."})
                
            if total >= 20:
                if wr >= 60:
                    tags.append({"text": "Smurf Şüphesi 🚨", "class": "bg-danger text-light", "tip": "Kazanma oranı tehlikeli derecede yüksek!"})
                elif wr <= 45:
                    tags.append({"text": "Ağır Yük 🧱", "class": "bg-dark text-light border border-secondary", "tip": "Takımı aşağı çekebilir."})

            player_data = {
                "puuid": p_puuid,
                "isim": get_riot_id_by_puuid(p_puuid, api_key),
                "sampiyon": c_info['name'],
                "ikon": c_info['icon'],
                "spell1": SPELLS.get(s1_id, ''),
                "spell2": SPELLS.get(s2_id, ''),
                "s1_id": s1_id, 
                "s2_id": s2_id,
                "main_rune": RUNES.get(p_ids[0], '') if p_ids else '',
                "sub_tree": TREES.get(p.get('perks', {}).get('perkSubStyle'), ''),
                "rank": rank_info,
                "mastery": mastery_data['text'],
                "hot_streak": rank_info.get('hot_streak', False), # YENİ
                "tags": tags # YENİ
            }
            
            if team_id == 100: match_info["blue_team"].append(player_data)
            else: match_info["red_team"].append(player_data)
            
        def assign_roles(team):
            roles = {0: None, 1: None, 2: None, 3: None, 4: None}
            unassigned = []
            for p in team:
                if 11 in [p['s1_id'], p['s2_id']] and roles[1] is None: roles[1] = p
                else: unassigned.append(p)
                    
            def assign_if(role_id, condition):
                for p in unassigned[:]: 
                    if roles[role_id] is None and condition(p):
                        roles[role_id] = p
                        unassigned.remove(p)
                        return True
                return False
                
            adc_list = ["Ashe", "Caitlyn", "Draven", "Ezreal", "Jhin", "Jinx", "Kai'Sa", "Kalista", "Kog'Maw", "Lucian", "Miss Fortune", "Nilah", "Samira", "Sivir", "Smolder", "Tristana", "Twitch", "Varus", "Vayne", "Xayah", "Zeri", "Aphelios"]
            sup_list = ["Lulu", "Karma", "Nami", "Janna", "Soraka", "Sona", "Thresh", "Nautilus", "Leona", "Rell", "Rakan", "Pyke", "Braum", "Taric", "Blitzcrank", "Alistar", "Renata Glasc", "Milio", "Yuumi", "Bard", "Senna"]
            
            assign_if(3, lambda p: p['sampiyon'] in adc_list)
            assign_if(4, lambda p: p['sampiyon'] in sup_list)
            assign_if(0, lambda p: 12 in [p['s1_id'], p['s2_id']])
            assign_if(3, lambda p: 7 in [p['s1_id'], p['s2_id']])
            assign_if(4, lambda p: 3 in [p['s1_id'], p['s2_id']])
            
            for i in range(5):
                if roles[i] is None and unassigned: roles[i] = unassigned.pop(0)
            return [roles[i] for i in range(5) if roles[i] is not None]

        match_info["blue_team"] = assign_roles(match_info["blue_team"])
        match_info["red_team"] = assign_roles(match_info["red_team"])
            
        with sqlite3.connect("lol_cache.db") as conn:
            conn.cursor().execute("REPLACE INTO match_cache (puuid, match_data, timestamp) VALUES (?, ?, ?)", (puuid, json.dumps(match_info), time.time()))
            conn.commit()
        return match_info, None, False
    elif response.status_code == 429:
        return None, "API limiti doldu! Lütfen bekleyin.", False
    else:
        return None, "Oyuncu şu an maçta değil.", False

# YENİ ROTA: Lazy Load (Tembel Yükleme) ile KDA Sorgulama
@app.route("/api/kda/<puuid>")
def api_kda(puuid):
    with sqlite3.connect("lol_cache.db") as conn:
        c = conn.cursor()
        c.execute("SELECT kda_data, timestamp FROM kda_cache WHERE puuid=?", (puuid,))
        row = c.fetchone()
        if row and (time.time() - row[1]) < CACHE_TTL_KDA:
            return jsonify(json.loads(row[0]))

    headers = {"X-Riot-Token": API_KEY}
    # Son 3 maçın ID'sini çeker
    ids_url = f"https://europe.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids?start=0&count=3"
    res = requests.get(ids_url, headers=headers)
    
    if res.status_code != 200:
        return jsonify({"error": "Sınır Aşıldı"}), 429

    match_ids = res.json()
    if not match_ids:
        return jsonify({"error": "Maç Yok"}), 404

    k, d, a = 0, 0, 0
    valid_matches = 0
    
    # 3 maçı tek tek indirip Kill/Death/Assist toplar
    for mid in match_ids:
        m_url = f"https://europe.api.riotgames.com/lol/match/v5/matches/{mid}"
        m_res = requests.get(m_url, headers=headers)
        if m_res.status_code == 200:
            m_data = m_res.json()
            for p in m_data.get('info', {}).get('participants', []):
                if p.get('puuid') == puuid:
                    k += p.get('kills', 0)
                    d += p.get('deaths', 0)
                    a += p.get('assists', 0)
                    valid_matches += 1
                    break

    if valid_matches == 0:
        return jsonify({"error": "Veri Alınamadı"}), 404

    kda_str = f"{k/valid_matches:.1f} / {d/valid_matches:.1f} / {a/valid_matches:.1f}"
    result = {"kda": kda_str, "matches": valid_matches}

    with sqlite3.connect("lol_cache.db") as conn:
        conn.cursor().execute("REPLACE INTO kda_cache (puuid, kda_data, timestamp) VALUES (?, ?, ?)", (puuid, json.dumps(result), time.time()))
        conn.commit()

    return jsonify(result)

# YENİ ROTA: Gizlilik Politikası Sayfası
@app.route("/privacy")
def privacy():
    return render_template("privacy.html")

# YENİ ROTA: Kullanım Koşulları Sayfası
@app.route("/terms")
def terms():
    return render_template("terms.html")

@app.route("/", methods=["GET", "POST"])
def index():
    match_data, error_msg, aranan_kisi, warning_msg = None, None, None, None
    if request.method == "POST":
        riot_id = request.form.get("riot_id")
        force_update = request.form.get("force_update") == "true"
        if riot_id and "#" in riot_id:
            aranan_kisi = riot_id
            try:
                game_name, tag_line = riot_id.split("#", 1)
                oyuncu_puuid = get_puuid(game_name, tag_line, API_KEY)
                if oyuncu_puuid:
                    match_data, error_msg, is_cooldown = get_live_match_data(oyuncu_puuid, API_KEY, force_update)
                    if is_cooldown: warning_msg = "Spam Koruması: Verileri sadece 2 dakikada bir güncelleyebilirsiniz."
                else: error_msg = "Hesap bulunamadı."
            except Exception as e: error_msg = f"Hata: {str(e)}"
        else: error_msg = "Lütfen İsim#Etiket formatında girin."
    return render_template("index.html", match_data=match_data, error=error_msg, aranan_kisi=aranan_kisi, warning=warning_msg)

if __name__ == "__main__":
    app.run(debug=True)