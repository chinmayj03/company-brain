FROM openjdk:17-jdk-slim AS build
WORKDIR /app
COPY pom.xml .
COPY src ./src
RUN ./mvnw -q package

FROM openjdk:17-jre-slim
WORKDIR /app
COPY --from=build /app/target/network-iq.jar /app/app.jar
EXPOSE 8080
ENTRYPOINT ["java", "-jar", "/app/app.jar"]
CMD ["--spring.profiles.active=prod"]
