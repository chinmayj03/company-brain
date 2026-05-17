package com.example.dao;

import org.springframework.jdbc.core.JdbcTemplate;

public class InventoryDao {

    private final JdbcTemplate jdbcTemplate;

    public InventoryDao(JdbcTemplate jdbcTemplate) {
        this.jdbcTemplate = jdbcTemplate;
    }

    public int decrementStock(String productId, int quantity) {
        return jdbcTemplate.update(
            "UPDATE inventory SET quantity = quantity - ? WHERE product_id = ? AND quantity >= ?",
            quantity, productId, quantity
        );
    }
}
