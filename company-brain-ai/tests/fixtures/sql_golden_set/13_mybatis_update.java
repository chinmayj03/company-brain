package com.example.mapper;

import org.apache.ibatis.annotations.Update;
import org.apache.ibatis.annotations.Param;

public interface UserMapper {

    @Update("UPDATE users SET email = #{email}, updated_at = now() WHERE id = #{id}")
    int updateEmail(@Param("id") String id, @Param("email") String email);
}
