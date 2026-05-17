package com.example.dao;

import org.springframework.jdbc.core.JdbcTemplate;
import java.util.List;

public class ProductDao {

    private final JdbcTemplate jdbcTemplate;

    public ProductDao(JdbcTemplate jdbcTemplate) {
        this.jdbcTemplate = jdbcTemplate;
    }

    public List<Product> findByCategory(String category) {
        return jdbcTemplate.query(
            "SELECT id, name, price, category FROM products WHERE category = ?",
            (rs, rowNum) -> new Product(rs.getString("id"), rs.getString("name")),
            category
        );
    }
}
