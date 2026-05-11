package com.example;

import jakarta.persistence.*;

@Entity
@Table(name = "users")
public class Competitor {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(nullable = false)
    private String lob;

    @Column(name = "email", nullable = false)
    private String email;

    @Column(name = "provider_type")
    private String providerType;
}
