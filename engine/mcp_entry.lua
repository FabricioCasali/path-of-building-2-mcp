-- mcp_entry.lua — persistent headless driver for the PoB2 MCP server.
--
-- Boots the engine ONCE, then serves a line-based JSON protocol on stdin/stdout:
--   request : {"id": <any>, "cmd": "<name>", ...args}          (one JSON per line)
--   response: {"id": <same>, "ok": true, "result": {...}}
--             {"id": <same>, "ok": false, "error": "message"}
--
-- The engine is kept hot between commands (init is the expensive part), so the
-- Python MCP server should spawn this once and reuse it.
--
-- Run from the `src` dir with the runtime on package.path (see SPIKE-RESULTS.md):
--   luajit mcp_entry.lua
--
-- stdout is RESERVED for JSON responses. All engine chatter (tree loading,
-- "missing node", ConPrintf, etc.) is redirected to stderr so it never
-- corrupts the protocol.

local real_stdout = io.stdout

-- Redirect every print()/ConPrintf() to stderr BEFORE booting the engine.
_G.print = function(...)
	local n = select("#", ...)
	local parts = {}
	for i = 1, n do parts[i] = tostring((select(i, ...))) end
	io.stderr:write(table.concat(parts, "\t"), "\n")
end

-- Boot: defines globals build, newBuild, loadBuildFromXML, runCallback, ...
dofile("HeadlessWrapper.lua")

local dkjson = require("dkjson")

---------------------------------------------------------------------------
-- Helpers
---------------------------------------------------------------------------

-- Deep, crash-proof field read: safeget(build, "spec", "curClassName").
-- Returns nil if any hop is missing or errors.
local function safeget(root, ...)
	local keys = { ... }
	local ok, cur = pcall(function()
		local node = root
		for _, k in ipairs(keys) do
			if type(node) ~= "table" then return nil end
			node = node[k]
		end
		return node
	end)
	if ok then return cur end
	return nil
end

-- Only emit finite numbers; JSON can't carry inf/nan and dkjson would choke.
local function num(v)
	if type(v) ~= "number" then return nil end
	if v ~= v then return nil end          -- nan
	if v == math.huge or v == -math.huge then return nil end
	return v
end

local function respond(obj)
	local encoded = dkjson.encode(obj)
	real_stdout:write(encoded, "\n")
	real_stdout:flush()
end

-- Recalculate the whole output after a config/build change.
local function recalc()
	-- Wipe the per-skill result cache; otherwise a config change can reuse cached
	-- DPS output and the new mods never show up (newBuild() wipes it too).
	if GlobalCache and GlobalCache.cachedData and wipeGlobalCache then
		wipeGlobalCache()
	end
	if build and build.configTab then
		build.configTab:BuildModList()
	end
	-- Rebuild the output DIRECTLY. runCallback("OnFrame") does not reliably
	-- rebuild mainOutput for a freshly-imported build (the buildFlag path stays
	-- cached), so BuildOutput() is what actually folds in the new config mods.
	if build and build.calcsTab then
		build.calcsTab:BuildOutput()
	end
end

-- true once a build is actually loaded (mainOutput populated).
local function build_ready()
	return safeget(build, "calcsTab", "mainOutput") ~= nil
end

---------------------------------------------------------------------------
-- Output collectors
---------------------------------------------------------------------------

local function summary()
	return {
		level      = num(safeget(build, "characterLevel")),
		className  = safeget(build, "spec", "curClassName"),
		ascendancy = safeget(build, "spec", "curAscendClassName"),
		mainSkill  = safeget(build, "calcsTab", "mainSkill", "activeEffect", "grantedEffect", "name"),
	}
end

local function offense_stats()
	local o = safeget(build, "calcsTab", "mainOutput") or {}
	return {
		TotalDPS       = num(o.TotalDPS),
		CombinedDPS    = num(o.CombinedDPS),
		AverageHit     = num(o.AverageHit),
		Speed          = num(o.Speed),
		CritChance     = num(o.CritChance),
		CritMultiplier = num(o.CritMultiplier),
	}
end

local function pools_and_resists()
	local o = safeget(build, "calcsTab", "mainOutput") or {}
	return {
		Life            = num(o.Life),
		Mana            = num(o.Mana),
		EnergyShield    = num(o.EnergyShield),
		Ward            = num(o.Ward),
		TotalEHP        = num(o.TotalEHP),
		EHPSurvivalTime = num(o.EHPSurvivalTime),
		FireResist      = num(o.FireResistTotal),
		ColdResist      = num(o.ColdResistTotal),
		LightningResist = num(o.LightningResistTotal),
		ChaosResist     = num(o.ChaosResistTotal),
	}
end

local function defenses()
	local m = safeget(build, "calcsTab", "mainOutput") or {}
	local c = safeget(build, "calcsTab", "calcsOutput") or {}
	return {
		Life         = num(m.Life),
		Mana         = num(m.Mana),
		EnergyShield = num(m.EnergyShield),
		Ward         = num(m.Ward),
		Armour       = num(m.Armour),
		Evasion      = num(m.Evasion),
		BlockChance  = num(m.BlockChance),
		TotalEHP     = num(m.TotalEHP),
		MaximumHitTaken = {
			Physical  = num(c.PhysicalMaximumHitTaken),
			Fire      = num(c.FireMaximumHitTaken),
			Cold      = num(c.ColdMaximumHitTaken),
			Lightning = num(c.LightningMaximumHitTaken),
			Chaos     = num(c.ChaosMaximumHitTaken),
		},
	}
end

---------------------------------------------------------------------------
-- Skill / gem helpers
---------------------------------------------------------------------------

-- The headline DPS number. FullDPS is only populated when a group is flagged
-- includeInFullDPS; fall back to CombinedDPS/TotalDPS which are always set.
local function main_dps()
	local o = safeget(build, "calcsTab", "mainOutput") or {}
	return num(o.FullDPS and o.FullDPS > 0 and o.FullDPS or nil)
		or num(o.CombinedDPS) or num(o.TotalDPS) or 0
end

-- Describe one gem instance as a plain table.
local function gem_view(g)
	return {
		name    = g.nameSpec,
		gemId   = g.gemId,
		skillId = g.skillId,
		level   = num(g.level),
		quality = num(g.quality),
		enabled = g.enabled and true or false,
		support = (g.gemData and g.gemData.grantedEffect and g.gemData.grantedEffect.support) and true or false,
	}
end

-- Human-readable name of a socket group's active (main) skill, if resolved.
local function group_active_skill_name(sg)
	local as = sg.displaySkillList and sg.displaySkillList[sg.mainActiveSkill or 1]
	return safeget(as, "activeEffect", "grantedEffect", "name")
end

-- Resolve a socket-group index from the request (1-based); default = main group.
local function resolve_group(req)
	local idx = req.group or build.mainSocketGroup or 1
	local sg = safeget(build, "skillsTab", "socketGroupList", idx)
	return sg, idx
end

-- Reprocess a group after mutating its gemList, then full recalc.
local function reprocess_and_recalc(sg)
	build.skillsTab:ProcessSocketGroup(sg)
	recalc()
end

-- Build a fresh gem instance table from resolved gemData at a given level/quality.
local function make_gem(gemData, level, quality)
	return {
		gemId = gemData.id,
		skillId = gemData.grantedEffectId,
		nameSpec = gemData.name,
		level = level or gemData.naturalMaxLevel or 20,
		quality = quality or 0,
		enabled = true,
		enableGlobal1 = true,
		enableGlobal2 = true,
		count = 1,
	}
end

---------------------------------------------------------------------------
-- Command handlers: each returns (result, errString)
---------------------------------------------------------------------------

local handlers = {}

function handlers.ping()
	return { pong = true }
end

-- {cmd="import_build", xml="<raw build xml>", name?="..."}
function handlers.import_build(req)
	if type(req.xml) ~= "string" or req.xml == "" then
		return nil, "import_build requires a non-empty 'xml' string"
	end
	loadBuildFromXML(req.xml, req.name or "MCP Build")
	if not build_ready() then
		return nil, "build failed to load (mainOutput missing)"
	end
	return summary()
end

-- {cmd="import_character", json="<raw body of the PoE character API>"}
-- Build from a live character fetched off pathofexile.com. The Python server
-- does the (authenticated) HTTP; here we just decode the JSON and reuse PoB's
-- own importer (same path as HeadlessWrapper.loadBuildFromJSON). The PoE2
-- character endpoint returns equipment + passives + jewels in one object, so
-- the same decoded table feeds both import steps.
function handlers.import_character(req)
	if type(req.json) ~= "string" or req.json == "" then
		return nil, "import_character requires a non-empty 'json' string"
	end
	local data, _, decodeErr = dkjson.decode(req.json)
	if decodeErr or type(data) ~= "table" then
		return nil, "could not decode character JSON: " .. tostring(decodeErr)
	end
	local charData = data.character or data
	if type(charData) ~= "table" or (charData.equipment == nil and charData.passives == nil) then
		return nil, "character JSON has no 'equipment'/'passives' (wrong payload?)"
	end

	newBuild()
	build.importTab:ImportPassiveTreeAndJewels(charData)
	build.calcsTab:BuildOutput()
	build.importTab:ImportItemsAndSkills(charData)
	recalc()

	if not build_ready() then
		return nil, "character imported but build did not become ready"
	end
	local res = summary()
	-- Hand the composed XML back so the server can remember the build (and the
	-- caller can turn it into a share code).
	local ok, xml = pcall(function() return build:SaveDB("code") end)
	if ok and type(xml) == "string" then res.xml = xml end
	return res
end

-- {cmd="export_xml"} -> the current build serialized to PoB XML.
function handlers.export_xml()
	if not build_ready() then return nil, "no build loaded" end
	local ok, xml = pcall(function() return build:SaveDB("code") end)
	if not ok or type(xml) ~= "string" then
		return nil, "failed to serialize build XML"
	end
	return { xml = xml }
end

-- {cmd="calc_stats"}  -> summary + offense + pools/resists
function handlers.calc_stats()
	if not build_ready() then return nil, "no build loaded" end
	local res = summary()
	res.offense = offense_stats()
	res.defense = pools_and_resists()
	return res
end

-- {cmd="get_defenses"}
function handlers.get_defenses()
	if not build_ready() then return nil, "no build loaded" end
	return defenses()
end

-- {cmd="set_config", customMods="100% increased maximum Mana"}
-- customMods may be a string or an array of strings (joined by newlines).
function handlers.set_config(req)
	if not build_ready() then return nil, "no build loaded" end
	local mods = req.customMods
	if type(mods) == "table" then
		mods = table.concat(mods, "\n")
	end
	if type(mods) ~= "string" then
		return nil, "set_config requires 'customMods' (string or array of strings)"
	end
	-- Write to the ACTIVE config set's input table, which is what BuildModList
	-- reads. After loadBuildFromXML the `configTab.input` alias can be stale
	-- (Load rebuilds configSets), so targeting it directly would be a no-op.
	local cfg = build.configTab
	local activeInput = cfg.configSets[cfg.activeConfigSetId].input
	activeInput.customMods = mods
	cfg.input = activeInput  -- refresh the alias to keep the rest of PoB consistent
	recalc()
	local res = summary()
	res.offense = offense_stats()
	res.defense = pools_and_resists()
	return res
end

-- {cmd="list_skills"} -> socket groups with their gems and which is the main group.
function handlers.list_skills()
	if not build_ready() then return nil, "no build loaded" end
	local groups = {}
	local list = safeget(build, "skillsTab", "socketGroupList") or {}
	for i, sg in ipairs(list) do
		local gems = {}
		for _, g in ipairs(sg.gemList or {}) do
			gems[#gems + 1] = gem_view(g)
		end
		groups[#groups + 1] = {
			index = i,
			label = sg.label,
			slot = sg.slot,
			enabled = sg.enabled and true or false,
			activeSkill = group_active_skill_name(sg),
			mainActiveSkill = num(sg.mainActiveSkill),
			gems = gems,
		}
	end
	return {
		mainSocketGroup = num(build.mainSocketGroup),
		mainDPS = main_dps(),
		groups = groups,
	}
end

-- {cmd="set_main_skill", group=<index>, activeSkill?=<index within group>}
function handlers.set_main_skill(req)
	if not build_ready() then return nil, "no build loaded" end
	local list = safeget(build, "skillsTab", "socketGroupList") or {}
	local idx = req.group
	if type(idx) ~= "number" or not list[idx] then
		return nil, "set_main_skill requires a valid 'group' index"
	end
	build.mainSocketGroup = idx
	if type(req.activeSkill) == "number" then
		list[idx].mainActiveSkill = req.activeSkill
	end
	build.buildFlag = true
	recalc()
	return { mainSocketGroup = idx, activeSkill = group_active_skill_name(list[idx]), mainDPS = main_dps() }
end

-- {cmd="list_compatible_supports", group?=<index>}
-- Enumerate every support gem that can support the group's main active skill,
-- flagging which are already socketed.
function handlers.list_compatible_supports(req)
	if not build_ready() then return nil, "no build loaded" end
	local sg = resolve_group(req)
	if not sg then return nil, "invalid group" end
	local as = sg.displaySkillList and sg.displaySkillList[sg.mainActiveSkill or 1]
	if not as then return nil, "group has no resolved active skill" end

	local socketed = {}
	for _, g in ipairs(sg.gemList or {}) do
		if g.gemId then socketed[g.gemId] = true end
	end

	local supports = {}
	for gemId, gemData in pairs(build.data.gems) do
		local ge = gemData.grantedEffect
		if ge and ge.support and calcLib.canGrantedEffectSupportActiveSkill(ge, as) then
			-- "Lineage" supports (Tier 0, tags.lineage) are unique/rare item-tier
			-- supports, not the common buyable gems.
			local isLineage = (gemData.tags and gemData.tags.lineage) and true or false
			supports[#supports + 1] = {
				name = gemData.name,
				gemId = gemId,
				alreadySocketed = socketed[gemId] and true or false,
				maxLevel = num(gemData.naturalMaxLevel),
				tier = num(gemData.Tier),
				lineage = isLineage,
			}
		end
	end
	table.sort(supports, function(a, b) return a.name < b.name end)
	return { group = num(build.mainSocketGroup), activeSkill = group_active_skill_name(sg), count = #supports, supports = supports }
end

-- {cmd="add_support", name="Fire Penetration I", group?=, level?=, quality?=}
function handlers.add_support(req)
	if not build_ready() then return nil, "no build loaded" end
	if type(req.name) ~= "string" then return nil, "add_support requires 'name'" end
	local sg = resolve_group(req)
	if not sg then return nil, "invalid group" end
	local err, gemData = build.skillsTab:FindSkillGem(req.name)
	if err then return nil, err end
	table.insert(sg.gemList, make_gem(gemData, req.level, req.quality))
	reprocess_and_recalc(sg)
	return { added = gemData.name, mainDPS = main_dps() }
end

-- {cmd="remove_gem", group?=, gemIndex=<n>}  (index within the group's gemList)
function handlers.remove_gem(req)
	if not build_ready() then return nil, "no build loaded" end
	local sg = resolve_group(req)
	if not sg then return nil, "invalid group" end
	local gi = req.gemIndex
	if type(gi) ~= "number" or not sg.gemList[gi] then
		return nil, "remove_gem requires a valid 'gemIndex'"
	end
	local removed = sg.gemList[gi].nameSpec
	table.remove(sg.gemList, gi)
	reprocess_and_recalc(sg)
	return { removed = removed, mainDPS = main_dps() }
end

-- {cmd="swap_gem", group?=, gemIndex=<n>, name="<new gem>", level?=, quality?=}
-- Replace the gem at gemIndex with a freshly resolved one (keeps the slot).
function handlers.swap_gem(req)
	if not build_ready() then return nil, "no build loaded" end
	local sg = resolve_group(req)
	if not sg then return nil, "invalid group" end
	local gi = req.gemIndex
	if type(gi) ~= "number" or not sg.gemList[gi] then
		return nil, "swap_gem requires a valid 'gemIndex'"
	end
	if type(req.name) ~= "string" then return nil, "swap_gem requires 'name'" end
	local err, gemData = build.skillsTab:FindSkillGem(req.name)
	if err then return nil, err end
	local old = sg.gemList[gi].nameSpec
	sg.gemList[gi] = make_gem(gemData, req.level, req.quality)
	reprocess_and_recalc(sg)
	return { swapped = old, to = gemData.name, mainDPS = main_dps() }
end

-- {cmd="set_gem", group?=, gemIndex=<n>, level?=, quality?=, enabled?=}
-- Tweak an existing gem's level/quality/enabled in place.
function handlers.set_gem(req)
	if not build_ready() then return nil, "no build loaded" end
	local sg = resolve_group(req)
	if not sg then return nil, "invalid group" end
	local gi = req.gemIndex
	local g = type(gi) == "number" and sg.gemList[gi]
	if not g then return nil, "set_gem requires a valid 'gemIndex'" end
	if type(req.level) == "number" then g.level = req.level end
	if type(req.quality) == "number" then g.quality = req.quality end
	if type(req.enabled) == "boolean" then g.enabled = req.enabled end
	reprocess_and_recalc(sg)
	return { gem = gem_view(g), mainDPS = main_dps() }
end

---------------------------------------------------------------------------
-- Item / rune helpers + handlers
---------------------------------------------------------------------------

-- The currently-equipped Item object in a slot (or nil). The slot control's
-- selItemId is authoritative (SetSelItemId writes it).
local function equipped_item(slotName)
	local it = build.itemsTab
	local slot = it and it.slots and it.slots[slotName]
	local id = slot and slot.selItemId
	if id and id ~= 0 then return it.items[id], id end
	return nil, 0
end

-- Full offense+defense snapshot, used by simulate-style handlers.
local function full_snapshot()
	local res = summary()
	res.offense = offense_stats()
	res.defense = pools_and_resists()
	res.mainDPS = main_dps()
	return res
end

-- {cmd="list_items"} -> equipped item per slot.
function handlers.list_items()
	if not build_ready() then return nil, "no build loaded" end
	local slots = {}
	local it = build.itemsTab
	for _, slot in ipairs(it.orderedSlots or {}) do
		-- skip jewel sockets; only real gear slots hold gear we simulate
		if not slot.nodeId and not tostring(slot.slotName):match("Jewel Socket") then
			local item = equipped_item(slot.slotName)
			if item then
				slots[#slots + 1] = {
					slot = slot.slotName,
					name = item.title or item.name,
					rarity = item.rarity,
					base = item.baseName,
					runes = item.runes,
					socketCount = num(item.itemSocketCount),
				}
			end
		end
	end
	return { slots = slots, mainDPS = main_dps() }
end

-- {cmd="get_equipped", slot="Body Armour"} -> raw text of the equipped item.
function handlers.get_equipped(req)
	if not build_ready() then return nil, "no build loaded" end
	if type(req.slot) ~= "string" then return nil, "get_equipped requires 'slot'" end
	local item = equipped_item(req.slot)
	if not item then return { slot = req.slot, equipped = false } end
	return { slot = req.slot, equipped = true, name = item.title or item.name, raw = item.raw }
end

-- {cmd="equip_item", slot="Weapon 1", raw="<item text>"}
-- Parse raw item text, equip it in the slot, recalc, return full stats.
function handlers.equip_item(req)
	if not build_ready() then return nil, "no build loaded" end
	if type(req.slot) ~= "string" then return nil, "equip_item requires 'slot'" end
	if type(req.raw) ~= "string" or req.raw == "" then return nil, "equip_item requires 'raw' item text" end
	local it = build.itemsTab
	if not it.slots[req.slot] then return nil, "unknown slot: " .. req.slot end

	local item = new("Item", req.raw)
	if not item.base then
		return nil, "could not parse item (unknown base type?)"
	end
	it:AddItem(item, true)          -- add to pool, do not auto-equip elsewhere
	it.slots[req.slot]:SetSelItemId(item.id)
	it:PopulateSlots()
	build.buildFlag = true
	recalc()
	local res = full_snapshot()
	res.equipped = { slot = req.slot, name = item.title or item.name }
	return res
end

-- {cmd="list_valid_runes", slot="Weapon 1"} -> runes/soul cores valid for that item.
function handlers.list_valid_runes(req)
	if not build_ready() then return nil, "no build loaded" end
	if type(req.slot) ~= "string" then return nil, "list_valid_runes requires 'slot'" end
	local item = equipped_item(req.slot)
	if not item then return nil, "no item equipped in slot " .. req.slot end
	if not (item.itemSocketCount and item.itemSocketCount > 0) then
		return { slot = req.slot, socketCount = 0, runes = {} }
	end
	local valid = build.itemsTab:GetValidRunesForItem(item)
	local names = {}
	for _, r in ipairs(valid or {}) do
		if r.name and r.name ~= "None" then
			names[#names + 1] = { name = r.name, type = r.type, label = r.label }
		end
	end
	return { slot = req.slot, socketCount = num(item.itemSocketCount), current = item.runes, runes = names }
end

-- {cmd="set_rune", slot="Weapon 1", runeIndex=1, rune="Desert Rune"}
-- Set a rune in one of the equipped item's sockets ("None" to clear), recalc.
function handlers.set_rune(req)
	if not build_ready() then return nil, "no build loaded" end
	if type(req.slot) ~= "string" then return nil, "set_rune requires 'slot'" end
	local ri = req.runeIndex
	if type(ri) ~= "number" then return nil, "set_rune requires numeric 'runeIndex'" end
	if type(req.rune) ~= "string" then return nil, "set_rune requires 'rune' name (or 'None')" end
	local item = equipped_item(req.slot)
	if not item then return nil, "no item equipped in slot " .. req.slot end
	if not (item.itemSocketCount and ri >= 1 and ri <= item.itemSocketCount) then
		return nil, "runeIndex out of range (item has " .. tostring(item.itemSocketCount or 0) .. " sockets)"
	end
	item.runes = item.runes or {}
	item.runes[ri] = req.rune
	item:UpdateRunes()
	item:BuildAndParseRaw()
	build.buildFlag = true
	recalc()
	local res = full_snapshot()
	res.rune = { slot = req.slot, runeIndex = ri, rune = req.rune }
	return res
end

---------------------------------------------------------------------------
-- Main loop
---------------------------------------------------------------------------

-- Signal readiness on stderr; Python waits for this before sending commands.
io.stderr:write("MCP_ENTRY_READY\n")
io.stderr:flush()

for line in io.lines() do
	if line:match("%S") then  -- skip blank lines
		local req, _, decodeErr = dkjson.decode(line)
		if decodeErr or type(req) ~= "table" then
			respond({ id = nil, ok = false, error = "invalid JSON request: " .. tostring(decodeErr) })
		else
			local handler = handlers[req.cmd]
			if not handler then
				respond({ id = req.id, ok = false, error = "unknown cmd: " .. tostring(req.cmd) })
			else
				local ok, result, errString = pcall(handler, req)
				if not ok then
					-- handler threw
					respond({ id = req.id, ok = false, error = "handler error: " .. tostring(result) })
				elseif result == nil then
					respond({ id = req.id, ok = false, error = errString or "unknown error" })
				else
					respond({ id = req.id, ok = true, result = result })
				end
			end
		end
	end
end
