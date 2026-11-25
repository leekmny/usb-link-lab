-- ULAB v1. Passive structural analysis. No endpoint keys or crypto verification.
-- Also allow the same installed plugin to be supplied with TShark -X.
if Dissector.get("ulab") then return end
local ulab = Proto("ulab", "USBLinkLab v1")
local types = {[1]="OPEN", [2]="META", [3]="DATA", [4]="END", [5]="ACK", [6]="ERROR", [7]="DRAIN"}
local modes = {[0]="PLAINTEXT", [1]="AES256GCM", [2]="CHACHA20POLY1305", [3]="HMACSHA256"}
local hellos = {[1]="CH", [2]="SH", [3]="CF"}
local f = {
    record = ProtoField.string("ulab.record", "Record"),
    direction = ProtoField.string("ulab.direction", "Inferred direction"),
    authentication = ProtoField.string("ulab.authentication", "Authentication"),
    pair_stage = ProtoField.uint8("ulab.pair_stage", "Enrollment stage", base.DEC),
    handshake_type = ProtoField.uint8("ulab.handshake_type", "Handshake", base.DEC, hellos),
    handshake_hmac = ProtoField.bytes("ulab.handshake_hmac", "Observed handshake HMAC"),
    frame_len = ProtoField.uint32("ulab.frame_len", "Bytes after prefix", base.DEC),
    version = ProtoField.uint8("ulab.version", "Version", base.DEC),
    frame_type = ProtoField.uint8("ulab.frame_type", "Frame type", base.DEC, types),
    mode = ProtoField.uint8("ulab.mode", "Mode", base.DEC, modes),
    flags = ProtoField.uint8("ulab.flags", "Flags", base.HEX),
    sequence = ProtoField.uint64("ulab.sequence", "Sender sequence", base.DEC),
    item_id = ProtoField.bytes("ulab.item_id", "Item ID"),
    chunk_index = ProtoField.uint64("ulab.chunk_index", "Chunk index", base.DEC),
    body_len = ProtoField.uint32("ulab.body_len", "Original body bytes", base.DEC),
    header_hmac = ProtoField.bytes("ulab.header_hmac", "Observed header HMAC"),
    control_hmac = ProtoField.bytes("ulab.control_hmac", "Observed control HMAC"),
    data_hmac = ProtoField.bytes("ulab.data_hmac", "Observed item body HMAC"),
    aead_tag = ProtoField.bytes("ulab.aead_tag", "Observed AEAD tag"),
    derived_nonce = ProtoField.bytes("ulab.derived_nonce", "Derived AEAD nonce (from sequence)"),
    plaintext = ProtoField.bytes("ulab.plaintext", "Visible body bytes"),
    ciphertext = ProtoField.bytes("ulab.ciphertext", "Ciphertext"),
    invalid = ProtoField.string("ulab.invalid", "Malformed or incomplete stream")
}
ulab.fields = f

local ports = DissectorTable.get("tcp.port")
local registered = {}
ulab.prefs.port = Pref.uint("Windows-facing server port", 43123, "ULAB TCP destination on the gadget")
ulab.prefs.iproxy_port = Pref.uint("Pi loopback server port", 43124, "ULAB TCP destination on Pi loopback")
local function bind()
    for port in pairs(registered) do ports:remove(port, ulab) end
    registered = {}
    for _, port in ipairs({ulab.prefs.port, ulab.prefs.iproxy_port}) do
        if port > 0 and port < 65536 and not registered[port] then
            ports:add(port, ulab)
            registered[port] = true
        end
    end
end

-- Frame-number anchors are idempotent across repeated dissection/two-pass TShark.
-- After a malformed boundary, leave the rest of that stream opaque.
local stream_number = Field.new("tcp.stream")
local legacy_sessions = {}
function ulab.init() legacy_sessions = {} end

local function get_session(pinfo)
    if Conversation then
        local conversation = pinfo.conversation
        local state = conversation[ulab]
        if not state then
            state = {}
            conversation[ulab] = state
        end
        return state
    end
    -- Wireshark 4.4 exposes the TCP stream field but no Lua Conversation object.
    local field = stream_number()
    -- Unfiltered two-pass TShark can omit the tree and all Field extractors
    -- during its first, sizing-only pass. Request TCP reassembly even then.
    if not field then return {sizing = true} end
    local stream = tostring(field)
    legacy_sessions[stream] = legacy_sessions[stream] or {}
    return legacy_sessions[stream]
end

function ulab.dissector(tvb, pinfo, tree)
    local state = get_session(pinfo)
    if not state then return 0 end
    local direction = registered[pinfo.src_port] and "s2c" or "c2s"
    local off, total = 0, tvb:len()
    local number = pinfo.number
    if state.invalid and number > state.invalid then return 0 end

    local function invalid(reason)
        local node = tree:add(ulab, tvb(off), "ULAB: " .. reason)
        node:add(f.invalid, reason):set_generated()
        state.invalid = math.min(state.invalid or number, number)
    end
    local function need(size, full_record)
        if total - off >= size then return true end
        if tvb:reported_len() > total or pinfo.can_desegment == 0 then
            invalid("Incomplete record at capture boundary")
        else
            pinfo.desegment_offset = off
            -- For a fixed prefix only, request the next segment and then compute
            -- the full PDU length. A fixed partial-header request can prevent TCP
            -- from extending the same PDU when its length becomes known.
            pinfo.desegment_len = full_record and (size - total + off) or DESEGMENT_ONE_MORE_SEGMENT
        end
        return false
    end
    local function node(size, kind)
        pinfo.cols.protocol = "ULAB"
        local n = tree:add(ulab, tvb(off,size), "ULAB " .. kind)
        n:add(f.record, kind):set_generated()
        n:add(f.direction, direction):set_generated()
        n:add(f.authentication, "unverified"):set_generated()
        return n
    end

    while off < total do
        if not need(4) then return total end
        local size
        if tvb(off,4):string() == "ULAB" then
            if not need(9) then return total end
            local magic, typ = tvb(off,8):string(), tvb(off+8,1):uint()
            if magic == "ULABP001" then
                if not need(11) then return total end
                local bytes = tvb(off+9,2):uint()
                if typ < 1 or typ > 6 or (typ <= 4 and (bytes == 0 or bytes > 512))
                    or (typ >= 5 and bytes ~= 32) or ((typ%2 == 1) ~= (direction == "c2s"))
                    or (not state.sizing and typ > 1 and (not state["p"..(typ-1)] or state["p"..(typ-1)] > number))
                    or (state["p"..typ] and state["p"..typ] ~= number)
                    or (state.h1 and state.h1 <= number) then
                    invalid("Invalid enrollment framing or stage order") return total
                end
                size = 11 + bytes
                if not need(size, true) then return total end
                node(size, "enrollment"):add(f.pair_stage, tvb(off+8,1))
                state["p"..typ] = number
            elseif magic == "ULAB0001" then
                if not hellos[typ] or ((typ == 2) ~= (direction == "s2c"))
                    or (not state.sizing and typ > 1 and (not state["h"..(typ-1)] or state["h"..(typ-1)] > number))
                    or (state["h"..typ] and state["h"..typ] ~= number)
                    or (typ == 1 and state.p1 and state.p1 <= number
                        and (not state.p6 or state.p6 > number)) then
                    invalid("Missing or invalid handshake stage") return total
                end
                size = typ == 3 and 41 or 73
                if not need(size, true) then return total end
                local n = node(size, hellos[typ])
                n:add(f.handshake_type, tvb(off+8,1))
                n:add(f.handshake_hmac, tvb(off+size-32,32))
                state["h"..typ] = number
            else
                invalid("Unknown ULAB prefix") return total
            end
        else
            local length = tvb(off,4):uint()
            if not state.sizing and (not state.h3 or state.h3 > number) then
                invalid("Missing captured handshake or capture starts midstream") return total
            end
            if length < 72 or length > 1048576 then
                invalid("Application record length outside bounds") return total
            end
            if not need(44) then return total end
            local typ = tvb(off+5,1):uint()
            local mode = tvb(off+6,1):uint()
            local bytes = tvb(off+40,4):uint()
            local control = typ == 1 or typ >= 5
            local overhead = (control or mode == 3) and 32 or (mode == 0 and 0 or 16)
            local zero_item = tvb(off+16,16):raw() == string.rep("\0",16)
            local zero_index = tvb(off+32,8):raw() == string.rep("\0",8)
            if tvb(off+4,1):uint() ~= 1 or not types[typ] or not modes[mode]
                or tvb(off+7,1):uint() ~= 0 or tvb(off+8,4):uint() ~= 0
                or (zero_item ~= (typ == 6 or typ == 7)) or (typ >= 6 and mode ~= 0)
                or ((control or typ == 2) and not zero_index)
                or (typ == 3 and (bytes < 1 or bytes > 65536))
                or (typ ~= 3 and bytes > 4096) or length ~= 72+bytes+overhead then
                invalid("Inconsistent application header or body length") return total
            end
            size = length + 4
            if not need(size, true) then return total end
            local n = node(size, types[typ])
            n:add(f.frame_len,tvb(off,4))
            n:add(f.version,tvb(off+4,1))
            n:add(f.frame_type,tvb(off+5,1))
            n:add(f.mode,tvb(off+6,1))
            n:add(f.flags,tvb(off+7,1))
            n:add(f.sequence,tvb(off+8,8))
            n:add(f.item_id,tvb(off+16,16))
            n:add(f.chunk_index,tvb(off+32,8))
            n:add(f.body_len,tvb(off+40,4))
            n:add(f.header_hmac,tvb(off+44,32))
            if control or mode == 0 or mode == 3 then
                n:add(f.plaintext,tvb(off+76,bytes))
                if overhead > 0 then
                    n:add(control and f.control_hmac or f.data_hmac,tvb(off+76+bytes,32))
                end
            else
                n:add(f.ciphertext,tvb(off+76,bytes))
                n:add(f.aead_tag,tvb(off+76+bytes,16))
                local nonce = ByteArray.new("00000000")
                nonce:append(tvb(off+8,8):bytes())
                n:add(f.derived_nonce,nonce:tvb("Derived ULAB nonce")(0,12)):set_generated()
            end
        end
        off = off + size
    end
    return total
end

bind()
ulab.prefs_changed = bind
