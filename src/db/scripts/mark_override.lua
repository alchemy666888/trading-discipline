local status = redis.call("HGET", KEYS[1], "status")
if not status then
    return 0
end

if status ~= "OPEN" and status ~= "OPEN_OVERRIDE" then
    return 0
end

local active_breach = redis.call("GET", KEYS[5])
if active_breach ~= ARGV[1] then
    return -1
end

local user_response = redis.call("HGET", KEYS[4], "user_response")
if user_response then
    return -1
end

redis.call("HSET", KEYS[1], "status", "OPEN_OVERRIDE")
redis.call("SREM", KEYS[2], ARGV[2])
redis.call("SADD", KEYS[3], ARGV[2])
redis.call(
    "HSET",
    KEYS[4],
    "user_response",
    "justified",
    "response_at",
    ARGV[3],
    "justification",
    ARGV[4]
)
redis.call("SREM", KEYS[6], ARGV[1])
redis.call("DEL", KEYS[5])

return 1
