from flask import Flask, render_template, request
import requests
import time
import sqlite3
import json

app = Flask(__name__)

API_KEY = "RGAPI-c70b1245-4eb5-4e51-8316-f2240ef81835"

CACHE_TTL_PUUID = 30 * 24 * 60 * 60 
CACHE_TTL_RANK = 15 * 60            
CACHE_TTL_MATCH = 2 * 60 

QUEUE_MAP = {
    420: "Dereceli (Tek/Çift)",
    440: "Dereceli (Esnek)",
    400: "Sıralı Seçim",
    430: "Kapalı Seçim",
    450: "ARAM",
    490: "Hızlı Oyun",
    700: "Clash",
    830: "Yapay Zeka (Başlangıç)",
    840: "Yapay Zeka (Kolay)",
    850: "Yapay Zeka (Orta)",
    900: "URF",
    1700: "Arena"
}

def init_db():
    with sqlite3.connect("lol_cache.db") as conn:
        c = conn.cursor()
        c.execute("CREATE TABLE IF NOT EXISTS puuid_cache (riot_id TEXT PRIMARY KEY, puuid TEXT, timestamp REAL)")
        c.execute("CREATE TABLE IF NOT EXISTS riot_id_cache (puuid TEXT PRIMARY KEY, riot_id TEXT, timestamp REAL)")
        c.execute("CREATE TABLE IF NOT EXISTS rank_cache (puuid TEXT PRIMARY KEY, rank_data TEXT, timestamp REAL)")
        c.execute("CREATE TABLE IF NOT EXISTS match_cache (puuid TEXT PRIMARY KEY, match_data TEXT, timestamp REAL)")
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
    rune_dict = {}
    tree_dict = {}
    
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
        if row and (time.time() - row[1]) < CACHE_TTL_PUUID:
            return row[0]
            
    url = f"https://europe.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{game_name}/{tag_line}"
    headers = {"X-Riot-Token": api_key}
    response = requests.get(url, headers=headers)
    
    if response.status_code == 200:
        puuid = response.json().get('puuid')
        with sqlite3.connect("lol_cache.db") as conn:
            c = conn.cursor()
            c.execute("REPLACE INTO puuid_cache (riot_id, puuid, timestamp) VALUES (?, ?, ?)", (riot_id_key, puuid, time.time()))
            conn.commit()
        return puuid
    return None

def get_riot_id_by_puuid(puuid, api_key):
    if not puuid: return "Bilinmeyen"
    with sqlite3.connect("lol_cache.db") as conn:
        c = conn.cursor()
        c.execute("SELECT riot_id, timestamp FROM riot_id_cache WHERE puuid=?", (puuid,))
        row = c.fetchone()
        if row and (time.time() - row[1]) < CACHE_TTL_PUUID:
            return row[0]
            
    url = f"https://europe.api.riotgames.com/riot/account/v1/accounts/by-puuid/{puuid}"
    headers = {"X-Riot-Token": api_key}
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        data = response.json()
        riot_id = f"{data.get('gameName')}#{data.get('tagLine')}"
        with sqlite3.connect("lol_cache.db") as conn:
            c = conn.cursor()
            c.execute("REPLACE INTO riot_id_cache (puuid, riot_id, timestamp) VALUES (?, ?, ?)", (puuid, riot_id, time.time()))
            conn.commit()
        return riot_id
    return "Gizli Oyuncu"

def get_rank_info(puuid, api_key, force=False):
    default_rank = {"text": "Derecesiz", "color_class": "unranked", "icon": ""}
    if not puuid: return default_rank
    
    if not force:
        with sqlite3.connect("lol_cache.db") as conn:
            c = conn.cursor()
            c.execute("SELECT rank_data, timestamp FROM rank_cache WHERE puuid=?", (puuid,))
            row = c.fetchone()
            if row and (time.time() - row[1]) < CACHE_TTL_RANK:
                return json.loads(row[0])
            
    url = f"https://tr1.api.riotgames.com/lol/league/v4/entries/by-puuid/{puuid}"
    headers = {"X-Riot-Token": api_key}
    response = requests.get(url, headers=headers)
    
    if response.status_code == 200:
        data = response.json()
        if not data: return default_rank
            
        rank_data = default_rank
        for league in data:
            q_type = league.get('queueType')
            tier = league.get('tier', '').capitalize()
            rank = league.get('rank', '')
            wins, losses = league.get('wins', 0), league.get('losses', 0)
            total = wins + losses
            wr = int((wins / total) * 100) if total > 0 else 0
            color_class = tier.lower() if tier else "unranked"
            icon_url = f"https://raw.communitydragon.org/latest/plugins/rcp-fe-lol-shared-components/global/default/{color_class}.png" if color_class != "unranked" else ""
            
            if q_type == "RANKED_SOLO_5x5":
                rank_data = {"text": f"{tier} {rank} (%{wr} WR - {total} Maç)", "color_class": color_class, "icon": icon_url}
                break
            elif q_type == "RANKED_FLEX_SR":
                rank_data = {"text": f"Flex: {tier} {rank} (%{wr} WR)", "color_class": color_class, "icon": icon_url}
        
        with sqlite3.connect("lol_cache.db") as conn:
            c = conn.cursor()
            c.execute("REPLACE INTO rank_cache (puuid, rank_data, timestamp) VALUES (?, ?, ?)", (puuid, json.dumps(rank_data), time.time()))
            conn.commit()
        return rank_data
    return {"text": "Hata", "color_class": "unranked", "icon": ""}

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
    headers = {"X-Riot-Token": api_key}
    response = requests.get(url, headers=headers)
    
    if response.status_code == 200:
        data = response.json()
        
        queue_id = data.get('gameQueueConfigId')
        game_type = data.get('gameType')
        
        if game_type == "CUSTOM_GAME":
            gercek_mod = "Özel Oyun / Antrenman"
        elif queue_id and queue_id in QUEUE_MAP:
            gercek_mod = QUEUE_MAP[queue_id]
        else:
            gercek_mod = data.get('gameMode', 'Bilinmiyor')
            
        bans = {"blue": [], "red": []}
        for ban in data.get('bannedChampions', []):
            cid = ban.get('championId')
            if cid > 0:
                c_icon = CHAMPS.get(cid, {}).get('icon', '')
                if c_icon:
                    if ban.get('teamId') == 100:
                        bans["blue"].append(c_icon)
                    else:
                        bans["red"].append(c_icon)

        match_info = {"gameMode": gercek_mod, "blue_team": [], "red_team": [], "bans": bans}
        
        for p in data.get('participants', []):
            p_puuid = p.get('puuid')
            team_id = p.get('teamId')
            
            c_info = CHAMPS.get(p.get('championId'), {'name': 'Bilinmeyen', 'icon': ''})
            s1_id = p.get('spell1Id')
            s2_id = p.get('spell2Id')
            
            perks = p.get('perks', {})
            p_ids = perks.get('perkIds', [])
            
            player_data = {
                "isim": get_riot_id_by_puuid(p_puuid, api_key),
                "sampiyon": c_info['name'],
                "ikon": c_info['icon'],
                "spell1": SPELLS.get(s1_id, ''),
                "spell2": SPELLS.get(s2_id, ''),
                "s1_id": s1_id, 
                "s2_id": s2_id,
                "main_rune": RUNES.get(p_ids[0], '') if p_ids else '',
                "sub_tree": TREES.get(perks.get('perkSubStyle'), ''),
                "rank": get_rank_info(p_puuid, api_key, force=force_update) 
            }
            
            if team_id == 100:
                match_info["blue_team"].append(player_data)
            else:
                match_info["red_team"].append(player_data)
            
        # --- YENİ VE KUSURSUZ: KESİN SLOT DOLDURMA ALGORİTMASI ---
        def assign_roles(team):
            roles = {0: None, 1: None, 2: None, 3: None, 4: None}
            unassigned = []
            
            # 1. Çarpı olanı direkt Jungle (1) yap
            for p in team:
                if 11 in [p['s1_id'], p['s2_id']] and roles[1] is None:
                    roles[1] = p
                else:
                    unassigned.append(p)
                    
            def assign_if(role_id, condition):
                for p in unassigned[:]: # Kopyası üzerinden dönüyoruz ki silerken hata olmasın
                    if roles[role_id] is None and condition(p):
                        roles[role_id] = p
                        unassigned.remove(p)
                        return True
                return False
                
            adc_list = ["Ashe", "Caitlyn", "Draven", "Ezreal", "Jhin", "Jinx", "Kai'Sa", "Kalista", "Kog'Maw", "Lucian", "Miss Fortune", "Nilah", "Samira", "Sivir", "Smolder", "Tristana", "Twitch", "Varus", "Vayne", "Xayah", "Zeri", "Aphelios"]
            sup_list = ["Lulu", "Karma", "Nami", "Janna", "Soraka", "Sona", "Thresh", "Nautilus", "Leona", "Rell", "Rakan", "Pyke", "Braum", "Taric", "Blitzcrank", "Alistar", "Renata Glasc", "Milio", "Yuumi", "Bard", "Senna", "Nautilus"]
            
            # 2. Şampiyon isimlerine göre ADC ve SUP yerleştir
            assign_if(3, lambda p: p['sampiyon'] in adc_list)
            assign_if(4, lambda p: p['sampiyon'] in sup_list)
            
            # 3. Kalanlardan Işınlan alanları Top (0) yap
            assign_if(0, lambda p: 12 in [p['s1_id'], p['s2_id']])
            
            # 4. Hala yerleşmemişlerden Şifa alanları ADC, Bitkinlik alanları SUP yap
            assign_if(3, lambda p: 7 in [p['s1_id'], p['s2_id']])
            assign_if(4, lambda p: 3 in [p['s1_id'], p['s2_id']])
            
            # 5. Kalan oyuncuları boş olan koltuklara sırayla oturt
            for i in range(5):
                if roles[i] is None and unassigned:
                    roles[i] = unassigned.pop(0)
                    
            return [roles[i] for i in range(5) if roles[i] is not None]

        # Her iki takımı da bu yeni algoritmadan geçiriyoruz
        match_info["blue_team"] = assign_roles(match_info["blue_team"])
        match_info["red_team"] = assign_roles(match_info["red_team"])
            
        with sqlite3.connect("lol_cache.db") as conn:
            c = conn.cursor()
            c.execute("REPLACE INTO match_cache (puuid, match_data, timestamp) VALUES (?, ?, ?)", (puuid, json.dumps(match_info), time.time()))
            conn.commit()
            
        return match_info, None, False
    elif response.status_code == 429:
        return None, "API limiti doldu! Lütfen bekleyin.", False
    else:
        return None, "Oyuncu şu an maçta değil.", False

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
                    if is_cooldown:
                        warning_msg = "Spam Koruması: Verileri sadece 2 dakikada bir güncelleyebilirsiniz."
                else:
                    error_msg = "Hesap bulunamadı."
            except Exception as e:
                error_msg = f"Hata: {str(e)}"
        else:
            error_msg = "Lütfen İsim#Etiket formatında girin."

    return render_template("index.html", match_data=match_data, error=error_msg, aranan_kisi=aranan_kisi, warning=warning_msg)

if __name__ == "__main__":
    app.run(debug=True)