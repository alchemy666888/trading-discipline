local status = redis.call("HGET", KEYS[1], "status")
if not status then
    return 0
end

if status ~= "OPEN" and status ~= "OPEN_OVERRIDE" then
    return 0
end

if ARGV[4] == "" then
    local active_breach = redis.call("GET", KEYS[6])
    if active_breach then
        return -2
    end
end

if ARGV[4] ~= "" then
    local active_breach = redis.call("GET", KEYS[6])
    if active_breach ~= ARGV[4] then
        return -1
    end

    local user_response = redis.call("HGET", KEYS[7], "user_response")
    if user_response then
        return -1
    end
end

redis.call(
    "HSET",
    KEYS[1],
    "status",
    "CLOSED",
    "closed_at",
    ARGV[1],
    "close_price",
    ARGV[2],
    "realized_pnl",
    ARGV[3]
)
redis.call("SREM", KEYS[2], ARGV[6])
redis.call("SREM", KEYS[3], ARGV[6])
redis.call("SADD", KEYS[4], ARGV[6])
redis.call("ZADD", KEYS[5], ARGV[7], ARGV[6])

if ARGV[4] ~= "" then
    redis.call(
        "HSET",
        KEYS[7],
        "user_response",
        "closed",
        "response_at",
        ARGV[5]
    )
    redis.call("SREM", KEYS[8], ARGV[4])
    redis.call("DEL", KEYS[6])
end

return 1
