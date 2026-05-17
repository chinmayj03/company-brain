package com.example.mapper;

import org.apache.ibatis.annotations.Delete;
import org.apache.ibatis.annotations.Param;

public interface SessionMapper {

    @Delete("DELETE FROM sessions WHERE user_id = #{userId} AND expires_at < now()")
    int deleteExpiredSessions(@Param("userId") String userId);
}
