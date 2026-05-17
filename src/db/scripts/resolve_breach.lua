local active_breach = redis.call("GET", KEYS[3])
if active_breach ~= ARGV[1] then
    return 0
end

local user_response = redis.call("HGET", KEYS[1], "user_response")
if user_response then
    return 0
end

redis.call("HSET", KEYS[1], "user_response", ARGV[2], "response_at", ARGV[3])

if ARGV[4] ~= "" then
    redis.call("HSET", KEYS[1], "justification", ARGV[4])
end

redis.call("SREM", KEYS[2], ARGV[1])
redis.call("DEL", KEYS[3])

return 1
