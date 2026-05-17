package com.example.mapper;

import org.apache.ibatis.annotations.Select;
import org.apache.ibatis.annotations.Param;
import java.util.List;
import java.util.UUID;

public interface UserMapper {

    @Select("SELECT id, username, email, created_at FROM users WHERE id = #{userId}")
    User findById(@Param("userId") UUID userId);

    @Select("SELECT id, username, email FROM users WHERE workspace_id = #{workspaceId} ORDER BY username")
    List<User> findByWorkspace(@Param("workspaceId") UUID workspaceId);
}
