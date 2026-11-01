
CREATE FUNCTION set_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------- users
CREATE TABLE users (
    user_id            INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    telegram_user_id   BIGINT  NOT NULL UNIQUE,
    telegram_username  TEXT,                              -- last known @nick, may change
    timezone           TEXT    NOT NULL DEFAULT 'UTC',    -- IANA name, e.g. 'Europe/Moscow'
    basic_privacy_type TEXT    NOT NULL DEFAULT 'open'
        CHECK (basic_privacy_type IN ('open', 'private', 'unavailable')),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- --------------------------------------------------------------- groups
CREATE TABLE groups (
    group_id         INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    telegram_chat_id BIGINT  NOT NULL UNIQUE
        CHECK (telegram_chat_id < 0),                     
    group_name       TEXT,                                
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ----------------------------------------------------- group_memberships
CREATE TABLE group_memberships (
    group_id   INTEGER NOT NULL REFERENCES groups (group_id) ON DELETE CASCADE,
    user_id    INTEGER NOT NULL REFERENCES users (user_id)   ON DELETE CASCADE,
    status     TEXT    NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'left', 'kicked')),
    joined_at  TIMESTAMPTZ,                               
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),        
    PRIMARY KEY (group_id, user_id)
);
CREATE INDEX ON group_memberships (user_id);              

-- ------------------------------------------------------------ intervals
-- Weekly recurring free windows. group_id NULL = global, otherwise only for that chat.
-- If end_time < start_time the window crosses midnight; "day" is the day it STARTS.
CREATE TABLE intervals (
    interval_id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id     INTEGER  NOT NULL REFERENCES users (user_id)   ON DELETE CASCADE,
    group_id    INTEGER  REFERENCES groups (group_id)          ON DELETE CASCADE,
    day         SMALLINT NOT NULL CHECK (day BETWEEN 1 AND 7), -- ISO: 1 = Monday ... 7 = Sunday
    start_time  TIME     NOT NULL,
    end_time    TIME     NOT NULL,
    CHECK (start_time <> end_time)
);
CREATE INDEX ON intervals (user_id, day);

-- ------------------------------------------------ availability_exceptions
-- One-off overrides: "I'm busy on Friday 18-20" (is_free = false) or "free despite schedule" (true).
-- TIMESTAMPTZ carries the full date, so crossing midnight needs no special handling here.
CREATE TABLE availability_exceptions (
    exception_id   INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id        INTEGER     NOT NULL REFERENCES users (user_id) ON DELETE CASCADE,
    is_free        BOOLEAN     NOT NULL,
    start_datetime TIMESTAMPTZ NOT NULL,
    end_datetime   TIMESTAMPTZ NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (end_datetime > start_datetime)
);
CREATE INDEX ON availability_exceptions (user_id, start_datetime);
CREATE TRIGGER trg_exceptions_updated BEFORE UPDATE ON availability_exceptions
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ------------------------------------------------------------- meetings
CREATE TABLE meetings (
    meeting_id       INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_by       INTEGER NOT NULL REFERENCES users (user_id),
    group_id         INTEGER REFERENCES groups (group_id) ON DELETE SET NULL, -- NULL = created outside a chat
    status           TEXT    NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'proposed', 'confirmed', 'cancelled')),
    start_datetime   TIMESTAMPTZ,                         -- NULL until a slot is chosen
    duration_minutes INTEGER NOT NULL CHECK (duration_minutes > 0),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (status <> 'confirmed' OR start_datetime IS NOT NULL)
);
CREATE TRIGGER trg_meetings_updated BEFORE UPDATE ON meetings
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ------------------------------------------------- meeting_participants
CREATE TABLE meeting_participants (
    meeting_id  INTEGER NOT NULL REFERENCES meetings (meeting_id) ON DELETE CASCADE,
    user_id     INTEGER NOT NULL REFERENCES users (user_id)       ON DELETE CASCADE,
    status      TEXT    NOT NULL DEFAULT 'invited'
        CHECK (status IN ('invited', 'accepted', 'declined')),
    is_required BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (meeting_id, user_id)
);
CREATE TRIGGER trg_participants_updated BEFORE UPDATE ON meeting_participants
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- -------------------------------------------------------- user_relations
-- Directed link: user_id -> target_id ("user_id treats target_id as ...").
-- Direction matters: A can list B as a friend without B doing the same.
CREATE TABLE user_relations (
    user_id       INTEGER NOT NULL REFERENCES users (user_id) ON DELETE CASCADE,
    target_id     INTEGER NOT NULL REFERENCES users (user_id) ON DELETE CASCADE,
    relation_type TEXT CHECK (relation_type IN ('friend', 'blocked')), 
    PRIMARY KEY (user_id, target_id),
    CHECK (user_id <> target_id)
);
